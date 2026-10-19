"""Pinned public Internet Archive identities, not an artwork license grant."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str
    collection: str
    item: str
    name: str
    size: int
    sha1: str
    destination: str

    @property
    def url(self):
        from urllib.parse import quote
        return f'https://archive.org/download/{self.item}/{quote(self.name)}'


ESSENTIAL = Source('essential', 'essential-collection', 'animationfactoryessentialcollection',
                   'animation factory essential collection.zip',1693097965,
                   'b463bd39811c1710e2310d9d402b7511f7d14844','essential-collection')
ANIME = (
    (684578816,'cfe8b1bf884b391ebe75d99ae058c26f120ecbdf'),
    (678649856,'638701092be4b6934c8596b6fbaf329c0d43a502'),
    (654778368,'0e0540389d0f508d6983aa1423cdf1e728b7a391'),
    (676667392,'57b41ac8f8a2bdbbfb298681ad5a828a95d31068'),
    (680685568,'43667b65fd51ef212d23de03498d935967727d22'),
    (650604544,'129ad32f4bc2cc5f5b3833fe8c7b82f8d34f00e4'),
    (693125120,'c91f9fab4b4461405e22ba21cf1e1a6cd69e9cd1'),
    (700794880,'bf8e579a3760d455158848384e92fccb10e5ded2'),
    (686032896,'0bff1a23c73019caa597c3f41a044e102f80a45d'),
    (707225600,'fc0f1ec53ba4f20396ae96b6dba3244c2609a40e'),
    (722051072,'daa164ff9f886c66c8cba9983f04c5c611c7d78c'),
    (671639552,'04347edb580ce5d9ea4c73f9cbd760cb5adc5455'),
)
SOURCES = (ESSENTIAL, Source('250k', '250k-animations',
    '3DAnimatedClipart2AnimationFactory2008','250K_ANIMATIONS.iso',3806298112,
    'd4448d13af5c2b0f21f48decda73d34a5fb83c8e','250k-animations')) + tuple(
    Source(f'anime-{number:02}', 'anime-factory','anime-factory-1',
           f'Anime {"factory" if number in (8,9) else "Factory"} {number}.iso',
           size,sha1,f'anime-factory/disc-{number:02}')
    for number,(size,sha1) in enumerate(ANIME,1))
DOWNLOAD_BYTES = sum(source.size for source in SOURCES)
KNOWN_DAMAGED_SHA256 = '260a2e8ab33c3b47909b7e19e5fd465e9cc4a823b66b260ac1a92ef3ddcb8587'
