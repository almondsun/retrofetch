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

#define MAX_BYTES (128u * 1024u * 1024u)
static uint32_t u32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static uint64_t u64(const unsigned char *p) { return u32(p) | (uint64_t)u32(p+4)<<32; }
static int sha(const void *p, size_t n, unsigned char out[32]) {
    unsigned int length = 0;
    return EVP_Digest(p,n,out,&length,EVP_sha256(),NULL)==1 && length==32;
}
static int safe_path(const char *p) {
    char resolved[PATH_MAX];
    return p[0]=='/' && realpath(p,resolved) && strcmp(p,resolved)==0;
}
static int same_identity(const struct stat *a,const struct stat *b) {
    return a->st_dev==b->st_dev && a->st_ino==b->st_ino && a->st_size==b->st_size
        && a->st_mtim.tv_sec==b->st_mtim.tv_sec && a->st_mtim.tv_nsec==b->st_mtim.tv_nsec
        && a->st_ctim.tv_sec==b->st_ctim.tv_sec && a->st_ctim.tv_nsec==b->st_ctim.tv_nsec
        && S_ISREG(b->st_mode);
}
static int stable_file(int fd,const char *path,const struct stat *before) {
    struct stat after,named;
    return !fstat(fd,&after) && !stat(path,&named)
        && same_identity(before,&after) && same_identity(before,&named);
}
static unsigned char *read_file(const char *path, size_t *length, size_t limit, int nofollow) {
    int fd=open(path,O_RDONLY|O_NONBLOCK|(nofollow?O_NOFOLLOW:0));
    struct stat st;
    if(fd<0) return NULL;
    if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size<0 || (uint64_t)st.st_size>limit) { close(fd); return NULL; }
    size_t n=(size_t)st.st_size, done=0;
    unsigned char *data=malloc(n+1);
    if(!data) { close(fd); return NULL; }
    while(done<n) {
        ssize_t got=read(fd,data+done,n-done);
        if(got<0 && errno==EINTR) continue;
        if(got<=0) { free(data); close(fd); return NULL; }
        done+=(size_t)got;
    }
    unsigned char extra;
    ssize_t tail=read(fd,&extra,1);
    int stable=stable_file(fd,path,&st);
    close(fd);
    if(tail!=0 || !stable) { free(data); return NULL; }
    data[n]=0; *length=n; return data;
}
static int file_digest(const char *path,unsigned char out[32]) {
    int fd=open(path,O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    struct stat before;
    if(fd<0) return 0;
    if(fstat(fd,&before) || !S_ISREG(before.st_mode) || before.st_size<0
            || (uint64_t)before.st_size>MAX_BYTES) { close(fd); return 0; }
    EVP_MD_CTX *context=EVP_MD_CTX_new();
    int ok=0;
    if(!context || EVP_DigestInit_ex(context,EVP_sha256(),NULL)!=1) goto end;
    unsigned char buffer[65536];
    size_t remaining=(size_t)before.st_size;
    while(remaining) {
        size_t count=remaining<sizeof buffer?remaining:sizeof buffer;
        ssize_t got=read(fd,buffer,count);
        if(got<0 && errno==EINTR) continue;
        if(got<=0 || EVP_DigestUpdate(context,buffer,(size_t)got)!=1) goto end;
        remaining-=(size_t)got;
    }
    ssize_t tail;
    do { tail=read(fd,buffer,1); } while(tail<0 && errno==EINTR);
    unsigned int length=0;
    if(tail || !stable_file(fd,path,&before)
            || EVP_DigestFinal_ex(context,out,&length)!=1 || length!=32) goto end;
    ok=1;
end:
    EVP_MD_CTX_free(context); close(fd); return ok;
}
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
static int uniform(uint32_t count,uint32_t *out) {
    uint32_t value, threshold=(uint32_t)(-count)%count;
    do {
        size_t done=0;
        while(done<sizeof value) {
            ssize_t n=getrandom((unsigned char *)&value+done,sizeof value-done,0);
            if(n<0 && errno==EINTR) continue;
            if(n<=0) return 0;
            done+=(size_t)n;
        }
    } while(value<threshold);
    *out=value%count; return 1;
}
static unsigned char *eligible_bytes=NULL;
static uint32_t eligible_count=0;
static int eligible(uint32_t ordinal) {
    if(!eligible_bytes) return 1;
    for(uint32_t i=0;i<eligible_count;i++) if(u32(eligible_bytes+4*(size_t)i)==ordinal) return 1;
    return 0;
}
static int valid_utf8(const unsigned char *p,size_t n) {
    size_t i=0;
    while(i<n) {
        uint32_t value=p[i++]; unsigned int need=0; uint32_t minimum=0;
        if(value<128) continue;
        if(value>=194 && value<=223) { need=1; minimum=128; value&=31; }
        else if(value>=224 && value<=239) { need=2; minimum=2048; value&=15; }
        else if(value>=240 && value<=244) { need=3; minimum=65536; value&=7; }
        else return 0;
        if(n-i<need) return 0;
        for(unsigned int j=0;j<need;j++) { if((p[i]&192)!=128) return 0; value=(value<<6)|(p[i++]&63); }
        if(value<minimum || value>0x10ffff || (value>=0xd800 && value<=0xdfff)) return 0;
    }
    return 1;
}
/* Validate every binary boundary and key ordinal before using even one record. */
static int select_asset(const char *root,const char *query,char path[PATH_MAX],uint32_t *width,uint32_t *height) {
    char index[PATH_MAX], catalog[PATH_MAX]; size_t n=0;
    if(!join(index,root,"selection.v3.idx") || !join(catalog,root,"catalog.json")) return 0;
    unsigned char *b=read_file(index,&n,MAX_BYTES,1), digest[32];
    int ok=0;
    if(!b) return 0;
    if(n<104 || memcmp(b,"RTFXIDX3",8) || u32(b+8)!=3) goto end;
    uint32_t count=u32(b+12);
    uint64_t records=u64(b+48), metadata=u64(b+56), keys=u64(b+64);
    if(!count || count>1000000 || records!=(uint64_t)44*count || records>n-104 || metadata>n-104-records || keys!=n-104-records-metadata) goto end;
    if(!sha(b,n-32,digest) || memcmp(digest,b+n-32,32) || !file_digest(catalog,digest) || memcmp(digest,b+16,32)) goto end;
    for(uint32_t i=0;i<eligible_count;i++) if(u32(eligible_bytes+4*(size_t)i)>=count) goto end;
    for(uint32_t i=0;i<count;i++) {
        const unsigned char *record=b+72+(size_t)i*44;
        if(!u32(record+32) || !u32(record+36) || u32(record+40)<2) goto end;
    }
    uint32_t selected=0, matches[3]={0,0,0}, ordinals[3]={0,0,0};
    size_t position=72+(size_t)records+(size_t)metadata, limit=n-32;
    size_t qlen=query?strlen(query):0;
    while(position<limit) {
        if(limit-position<9) goto end;
        uint32_t len=u32(b+position); position+=4;
        if(!len || len>2*1024*1024 || len>limit-position-5) goto end;
        const unsigned char *key=b+position; position+=len;
        unsigned int role=b[position++]; uint32_t ordinal=u32(b+position); position+=4;
        if(role>2 || ordinal>=count || memchr(key,0,len) || !valid_utf8(key,len)) goto end;
        if(query && qlen==len && memcmp(query,key,len)==0 && eligible(ordinal)) {
            if(!matches[role]) { matches[role]=1; ordinals[role]=ordinal; }
            else if(ordinals[role]!=ordinal) matches[role]=2;
        }
    }
    if(query) {
        unsigned int role=0; while(role<3 && !matches[role]) role++;
        if(role==3 || matches[role]!=1) goto end;
        selected=ordinals[role];
    } else {
        if(eligible_bytes) {
            uint32_t chosen;
            if(!eligible_count || !uniform(eligible_count,&chosen)) goto end;
            selected=u32(eligible_bytes+4*(size_t)chosen);
            if(selected>=count) goto end;
        } else if(!uniform(count,&selected)) goto end;
    }
    const unsigned char *record=b+72+(size_t)selected*44;
    char relative[81]="animations/"; static const char hex[]="0123456789abcdef";
    for(size_t i=0;i<32;i++) { relative[11+i*2]=hex[record[i]>>4]; relative[12+i*2]=hex[record[i]&15]; }
    strcpy(relative+75,".gif");
    if(!join(path,root,relative) || !safe_path(path)) goto end;
    *width=u32(record+32); *height=u32(record+36); ok=1;
end:
    free(b); return ok;
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
static const char *environment_keys[]={"HOME","PATH","XDG_CONFIG_HOME","XDG_CONFIG_DIRS","XDG_STATE_HOME",
    "XDG_DATA_HOME","TERM","COLORTERM","CLICOLOR","CLICOLOR_FORCE","NO_COLOR","NO_CONFIG","RETROFETCH_DATA_DIR","TMUX","STY",NULL};
static int environment_key(const char *key) {
    for(size_t i=0;environment_keys[i];i++) if(!strcmp(key,environment_keys[i])) return 1;
    return 0;
}
static int take_string(unsigned char *b,size_t n,size_t *pos,char **value,int empty) {
    if(n-*pos<4) return 0;
    uint32_t length=u32(b+*pos); *pos+=4;
    if((!empty && !length) || length>1024*1024 || length>n-*pos || memchr(b+*pos,0,length)) return 0;
    char *s=malloc((size_t)length+1);
    if(!s) return 0;
    memcpy(s,b+*pos,length); s[length]=0; *pos+=length; *value=s; return 1;
}
/* PATH text can stay unchanged while an earlier executable appears. */
static int current_fastfetch(char resolved[PATH_MAX]) {
    const char *value=getenv("PATH");
    if(!value) value="/bin:/usr/bin";
    if(strlen(value)>1024*1024) return 0;
    char *copy=strdup(value), *cursor=copy, *directory;
    if(!copy) return 0;
    int found=0;
    while((directory=strsep(&cursor, ":"))!=NULL) {
        char candidate[PATH_MAX]; struct stat st;
        int length=snprintf(candidate,sizeof candidate,"%s/fastfetch",*directory?directory:".");
        if(length>0 && length<(int)sizeof candidate && access(candidate,X_OK)==0
                && stat(candidate,&st)==0 && !S_ISDIR(st.st_mode)) {
            found=realpath(candidate,resolved)!=NULL;
            break;
        }
    }
    free(copy); return found;
}
static int profile(const char *root,int columns,int rows,double cw,double ch,int *supported,int *padding) {
    char path[PATH_MAX]; size_t n=0;
    if(!join(path,root,"native-runtime.v1.bin")) return 0;
    unsigned char *b=read_file(path,&n,MAX_BYTES,1), digest[32];
    if(!b) return 0;
    int ok=0; size_t pos=56;
    if(n<88 || memcmp(b,"RTFXNAT1",8) || u32(b+8)!=1 || !sha(b,n-32,digest) || memcmp(digest,b+n-32,32)) goto end;
    uint32_t envs=u32(b+12), deps=u32(b+16), count=u32(b+20);
    if(envs>4096 || deps>128 || !count || count>1000000 || u32(b+24)!=(uint32_t)columns || u32(b+28)!=(uint32_t)rows) goto end;
    uint64_t bits=u64(b+32); double expected_cw, expected_ch;
    memcpy(&expected_cw,&bits,8); bits=u64(b+40); memcpy(&expected_ch,&bits,8);
    if(expected_cw!=cw || expected_ch!=ch || u32(b+48)>1) goto end;
    uint32_t pad=u32(b+52);
    if(pad!=UINT32_MAX && pad>10000) goto end;
    size_t actual_envs=0;
    for(size_t i=0;environment_keys[i];i++) if(getenv(environment_keys[i])) actual_envs++;
    if(actual_envs!=envs) goto end;
    char executable[PATH_MAX];
    int resolved_executable=current_fastfetch(executable), bound_executable=0;
    char *previous=NULL;
    for(uint32_t i=0;i<envs;i++) {
        char *key=NULL,*value=NULL;
        if(!take_string(b,n-32,&pos,&key,0) || !take_string(b,n-32,&pos,&value,1)) { free(key); free(value); free(previous); goto end; }
        const char *current=getenv(key);
        int valid=environment_key(key) && current && !strcmp(current,value) && (!previous || strcmp(previous,key)<0);
        free(previous); previous=key; free(value);
        if(!valid) { free(previous); goto end; }
    }
    free(previous);
    for(uint32_t i=0;i<deps;i++) {
        char *file=NULL;
        if(!take_string(b,n-32,&pos,&file,0)) goto end;
        if(file[0]!='/' || n-32-pos<73 || b[pos]>1) { free(file); goto end; }
        int exists=b[pos++], valid;
        if(exists) valid=file_digest(file,digest) && !memcmp(digest,b+pos,32);
        else { struct stat st; valid=lstat(file,&st)!=0 && errno==ENOENT; }
        pos+=32;
        struct stat identity;
        if(exists) {
            if(stat(file,&identity)) valid=0;
            else {
                uint64_t expected[5]={(uint64_t)identity.st_dev,(uint64_t)identity.st_ino,(uint64_t)identity.st_size,
                    (uint64_t)identity.st_mtim.tv_sec*1000000000u+(uint64_t)identity.st_mtim.tv_nsec,
                    (uint64_t)identity.st_ctim.tv_sec*1000000000u+(uint64_t)identity.st_ctim.tv_nsec};
                for(size_t k=0;k<5;k++) if(expected[k]!=u64(b+pos+8*k)) valid=0;
            }
        }
        if(valid && exists && resolved_executable) {
            char canonical[PATH_MAX];
            if(realpath(file,canonical) && !strcmp(canonical,executable)) bound_executable=1;
        }
        free(file); pos+=40;
        if(!valid) goto end;
    }
    if(u32(b+48) && !bound_executable) goto end;
    if((uint64_t)count*4!=n-32-pos) goto end;
    for(uint32_t i=0;i<count;i++) if(u32(b+pos+4*(size_t)i)>=1000000 || (i && u32(b+pos+4*(size_t)(i-1))>=u32(b+pos+4*(size_t)i))) goto end;
    eligible_bytes=malloc((size_t)count*4);
    if(!eligible_bytes) goto end;
    memcpy(eligible_bytes,b+pos,(size_t)count*4); eligible_count=count;
    *supported=(int)u32(b+48); *padding=pad==UINT32_MAX?-1:(int)pad; ok=1;
end:
    free(b); return ok;
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
static char *quote(const char *path) {
    int safe=1;
    for(const unsigned char *c=(const unsigned char *)path;*c;c++) {
        if(!((*c>='a' && *c<='z') || (*c>='A' && *c<='Z') || (*c>='0' && *c<='9') || strchr("_@%+=:,./-",*c))) safe=0;
    }
    if(safe) return strdup(path);
    size_t n=strlen(path); char *out=malloc(5*n+3);
    if(!out) return NULL;
    char *p=out; *p++='\'';
    for(size_t i=0;i<n;i++) {
        if(path[i]=='\'') { memcpy(p,"'\"'\"'",5); p+=5; }
        else *p++=path[i];
    }
    *p++='\''; *p=0; return out;
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
    char data[PATH_MAX],temp[PATH_MAX],root[PATH_MAX];
    if(directory) { if(!expanded(data,directory)) return fallback(argv); }
    else if(getenv("RETROFETCH_DATA_DIR") && *getenv("RETROFETCH_DATA_DIR")) {
        if(!expanded(data,getenv("RETROFETCH_DATA_DIR"))) return fallback(argv);
    } else if(!xdg(temp,"XDG_DATA_HOME",".local/share") || !join(data,temp,"retrofetch")) return fallback(argv);
    int supported=0,padding=-1,has_profile=0;
    if(realpath(data,root)) has_profile=profile(root,columns,rows,cw,ch,&supported,&padding);
    if(config_exists() && !has_profile) return fallback(argv);
    if(!animation && (!has_profile || !supported || custom_size)) return fallback(argv);
    char path[PATH_MAX]; uint32_t expected_w=0,expected_h=0;
    if(file) {
        if(!expanded(temp,file) || !realpath(temp,path)) return fallback(argv);
    } else {
        if(!realpath(data,root)) return fallback(argv);
        char *query=identifier?strdup(identifier):NULL;
        if(identifier && !query) return fallback(argv);
        if(query) for(char *p=query;*p;p++) *p=(char)tolower((unsigned char)*p);
        int selected=select_asset(root,query,path,&expected_w,&expected_h); free(query);
        if(!selected) return fallback(argv);
    }
    uint32_t width,height;
    if(!gif_size(path,&width,&height) || (expected_w && (expected_w!=width || expected_h!=height))) return fallback(argv);
    if(!animation && (width!=160 || height!=160)) return fallback(argv);
    if(bw>columns-1) bw=columns-1;
    if(bh>rows-2) bh=rows-2;
    double scale=fmin(1,fmin(bw*cw/width,bh*ch/height));
    int display_width=(int)(width*scale/cw);
    char window[128];
    if(animation) {
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
    double pixels=display_width?display_width*cw:fmin(width,bh*ch*width/height);
    if(fmin(pixels,pixels*height/width)<1) return fallback(argv);
    char dw[32],dh[32],pad[32];
    snprintf(dw,sizeof dw,"%d",display_width);
    snprintf(dh,sizeof dh,"%d",!display_width && height>bh*ch?bh:0);
    snprintf(pad,sizeof pad,"%d",padding);
    char *quoted=quote(path);
    if(!quoted) return fallback(argv);
    char *args[]={"fastfetch","--logo-type","kitty","--logo",quoted,"--logo-animation-frame","0","--logo-width",dw,"--logo-height",dh,NULL,NULL,NULL};
    if(padding>=0) { args[11]="--logo-padding-top"; args[12]=pad; }
    else if(!startup) fputs("retrofetch: layout missing, stale, or top-positioned; native placement retained (run --calibrate-layout for side-by-side centering)\n",stderr);
    int result=child(args); free(quoted);
    if(!result) return 0;
    if(!startup) {
        if(result==-127) fputs("retrofetch: fastfetch executable is unavailable; falling back to Fastfetch\n",stderr);
        else fprintf(stderr,"retrofetch: animation rendering failed (exit %d); falling back to Fastfetch\n",result);
    }
    char *plain[]={"fastfetch",NULL}; result=child(plain);
    if(result==-127) {
        if(!startup) fputs("retrofetch: fastfetch executable is unavailable\n",stderr);
        return 1;
    }
    return result;
}
