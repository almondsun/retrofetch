/* Optional read-only frontend. Unproven states delegate without output. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <openssl/evp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static int join(char out[PATH_MAX],const char *base,const char *tail) {
    int n=snprintf(out,PATH_MAX,"%s/%s",base,tail);
    return n>0 && n<PATH_MAX;
}
static int expanded(char out[PATH_MAX], const char *value) {
    const char *home=getenv("HOME");
    int n;
    if(value[0]=='~') {
        if(!home || (value[1] && value[1]!='/')) return 0;
        n=snprintf(out,PATH_MAX,"%s%s",home,value+1);
    } else n=snprintf(out,PATH_MAX,"%s",value);
    return n>0 && n<PATH_MAX;
}
static int xdg(char out[PATH_MAX],const char *key,const char *suffix) {
    const char *base=getenv(key), *home=getenv("HOME");
    if(base && *base) return expanded(out,base);
    return home && join(out,home,suffix);
}
static int fallback(char **argv) {
    char launcher[PATH_MAX];
#ifdef RETROFETCH_PYTHON_LAUNCHER
    if(snprintf(launcher,sizeof launcher,"%s",RETROFETCH_PYTHON_LAUNCHER)>=(int)sizeof launcher) return 127;
#else
    ssize_t n=readlink("/proc/self/exe",launcher,sizeof launcher-1);
    if(n<=0 || n>=(ssize_t)sizeof launcher-1) return 127;
    launcher[n]=0; char *slash=strrchr(launcher,'/');
    if(!slash || (size_t)(slash-launcher)+strlen("/retrofetch-python")>=sizeof launcher) return 127;
    strcpy(slash,"/retrofetch-python");
#endif
    argv[0]=launcher; execv(launcher,argv);
    fputs("retrofetch: Python fallback launcher is unavailable\n",stderr); return 127;
}
static int child(char *const args[]) {
    int errors[2];
    if(pipe2(errors,O_CLOEXEC)) return -127;
    pid_t pid=fork();
    if(pid<0) { close(errors[0]); close(errors[1]); return -127; }
    if(pid==0) {
        close(errors[0]);
        int fd=open("/dev/null",O_RDONLY);
        if(fd>=0 && dup2(fd,STDIN_FILENO)>=0) { close(fd); execvp(args[0],args); }
        int failure=errno;
        ssize_t sent=write(errors[1],&failure,sizeof failure); (void)sent;
        _exit(127);
    }
    close(errors[1]);
    int failure=0; ssize_t got;
    do { got=read(errors[0],&failure,sizeof failure); } while(got<0 && errno==EINTR);
    close(errors[0]);
    int status;
    while(waitpid(pid,&status,0)<0) { if(errno!=EINTR) return -127; }
    if(got!=0) return -127;
    return WIFEXITED(status)?WEXITSTATUS(status):-WTERMSIG(status);
}
static int gif_size(const char *path,uint32_t *w,uint32_t *h) {
    int fd=open(path,O_RDONLY|O_NONBLOCK); unsigned char header[10]; struct stat st;
    if(fd<0) return 0;
    ssize_t n=read(fd,header,sizeof header);
    int regular=fstat(fd,&st)==0 && S_ISREG(st.st_mode); close(fd);
    if(!regular || n!=10 || (memcmp(header,"GIF87a",6) && memcmp(header,"GIF89a",6))) return 0;
    *w=(uint32_t)header[6]|(uint32_t)header[7]<<8; *h=(uint32_t)header[8]|(uint32_t)header[9]<<8;
    return *w && *h;
}
static int positive_size(const char *s,int *w,int *h) {
    char *end; errno=0; long a=strtol(s,&end,10);
    if(errno || end==s || (*end!='x' && *end!='X') || a<=0 || a>INT_MAX) return 0;
    const char *tail=end+1; long z=strtol(tail,&end,10);
    if(errno || end==tail || *end || z<=0 || z>INT_MAX) return 0;
    *w=(int)a; *h=(int)z; return 1;
}
static int config_exists(void) {
    char base[PATH_MAX], path[PATH_MAX]; struct stat st;
    if(!xdg(base,"XDG_CONFIG_HOME",".config") || !join(path,base,"retrofetch/config.toml")) return 1;
    if(lstat(path,&st)==0) return 1;
    return errno!=ENOENT;
}
int main(int argc,char **argv) {
    int animation=0,startup=0,bw=20,bh=10,custom_size=0;
    const char *file=NULL,*directory=NULL,*identifier=NULL;
    for(int i=1;i<argc;i++) {
        const char *a=argv[i];
        if(!strcmp(a,"--animation-only")) animation=1;
        else if(!strcmp(a,"--startup")) startup=1;
        else if(!strcmp(a,"--file") && i+1<argc && !file) file=argv[++i];
        else if(!strcmp(a,"--data-dir") && i+1<argc && !directory) directory=argv[++i];
        else if(!strcmp(a,"--size") && i+1<argc && !custom_size) {
            if(!positive_size(argv[++i],&bw,&bh)) return fallback(argv);
            custom_size=1;
        } else if(a[0]!='-' && !identifier) identifier=a;
        else return fallback(argv);
    }
    if((file && identifier) || (file && file[0]=='-') || (directory && directory[0]=='-')) return fallback(argv);
    if(identifier) for(const unsigned char *p=(const unsigned char *)identifier;*p;p++) if(*p>127) return fallback(argv);
    const char *term=getenv("TERM"), *tmux=getenv("TMUX"), *sty=getenv("STY");
    if(!isatty(STDOUT_FILENO) || !term || strcmp(term,"xterm-kitty") || (tmux && *tmux) || (sty && *sty)) return startup?0:fallback(argv);
    struct winsize ws={0};
    if(ioctl(STDOUT_FILENO,TIOCGWINSZ,&ws)) { ws.ws_col=80; ws.ws_row=24; }
    int columns=ws.ws_col<2?2:ws.ws_col,rows=ws.ws_row<3?3:ws.ws_row;
    double cw=ws.ws_xpixel?(double)ws.ws_xpixel/columns:8, ch=ws.ws_ypixel?(double)ws.ws_ypixel/rows:16;
    if(!animation || !file || config_exists()) return fallback(argv);
    char path[PATH_MAX],temp[PATH_MAX];
        if(!expanded(temp,file) || !realpath(temp,path)) return fallback(argv);
    uint32_t width,height;
    if(!gif_size(path,&width,&height)) return fallback(argv);
    if(bw>columns-1) bw=columns-1;
    if(bh>rows-2) bh=rows-2;
    char window[128];
        int px=(int)(bw*cw),py=(int)(bh*ch);
        snprintf(window,sizeof window,"--use-window-size=%d,%d,%d,%d",bw,bh,px,py);
        char *args[]={"kitten","icat","--engine=builtin","--stdin=no","--align=left","--transfer-mode=stream","--passthrough=none","--loop=-1",window,path,NULL};
        int result=child(args);
        if(result==-127) {
            if(!startup) fputs("retrofetch: kitten executable is unavailable\n",stderr);
            return 1;
        }
        if(result && !startup) fprintf(stderr,"retrofetch: animation rendering failed (exit %d)\n",result);
        return result?2:0;
}
