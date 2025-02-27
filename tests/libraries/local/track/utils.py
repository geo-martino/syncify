import string
from datetime import datetime
from random import choice, randrange, randint

import mutagen
from dateutil.relativedelta import relativedelta

from musify.libraries.local.track import TRACK_CLASSES, LocalTrack
from tests.libraries.local.utils import remote_wrangler, path_track_resources
from tests.libraries.remote.spotify.utils import random_uri
from tests.utils import random_str, random_dt, random_genres


class MutagenMock(mutagen.FileType):
    class MutagenInfoMock(mutagen.StreamInfo):
        def __init__(self):
            self.length = randrange(int(10e4), int(6*10e5))  # 1 second to 10 minutes range
            self.channels = randrange(1, 5)
            self.bitrate = randrange(96, 1400) * 1000
            self.sample_rate = choice([44.1, 48, 88.2, 96]) * 1000

    # noinspection PyMissingConstructor
    def __init__(self):
        self.info = self.MutagenInfoMock()
        self.pictures = []

    def clear_pictures(self):
        self.pictures.clear()


# noinspection PyProtectedMember
def random_track[T: LocalTrack](cls: type[T] | None = None) -> T:
    """Generates a new, random track of the given class."""
    if cls is None:
        cls = choice(tuple(TRACK_CLASSES))

    title = random_str(30, 50)
    track_number = randrange(1, 20)

    file = MutagenMock()
    file.info.length = randint(30, 600)

    filename = f"{str(track_number).zfill(2)} - {title}"
    ext = choice(tuple(cls.valid_extensions))
    file.filename = str(path_track_resources.joinpath(random_str(30, 50)).with_name(filename).with_suffix(ext))

    track = cls(file=file, remote_wrangler=remote_wrangler)
    track._loaded = True

    track.title = title
    track.artists = [random_str(30, 50) for _ in range(randrange(1, 3))]
    track.album = random_str(30, 50)
    track.album_artist = random_str(30, 50)
    track.track_number = track_number
    track.track_total = randint(track.track_number, 20)
    track.genres = random_genres()
    track.date = random_dt().date()
    track.bpm = randint(6000, 15000) / 100
    track.key = choice(string.ascii_uppercase[:7])
    track.disc_number = randrange(1, 8)
    track.disc_total = randint(track.disc_number, 20)
    track.compilation = choice([True, False])
    track.comments = [random_str(20, 50) for _ in range(randrange(3))]

    has_uri = choice([True, False])
    track.uri = random_uri() if has_uri else remote_wrangler.unavailable_uri_dummy

    track.image_links = {}
    track.has_image = False

    track.date_added = datetime.now() - relativedelta(days=randrange(8, 20), hours=randrange(1, 24))
    track.last_played = datetime.now() - relativedelta(days=randrange(1, 6), hours=randrange(1, 24))
    track.play_count = randrange(200)
    track.rating = randrange(0, 100)

    return track


def random_tracks[T: LocalTrack](number: int | None = None, cls: type[T] | None = None) -> list[T]:
    """Generates a ``number`` of random tracks of the given class."""
    return [random_track(cls=cls) for _ in range(number or randrange(2, 20))]
