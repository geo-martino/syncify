from itertools import groupby
from random import choice, randrange
from typing import Callable

from syncify.enums.tags import TagName, PropertyName
from syncify.local.track import LocalTrack
from syncify.local.playlist.processor import TrackSort, ShuffleMode
from syncify.utils import strip_ignore_words
from tests.local.track.track import random_tracks


def test_sort_by_field():
    tracks = random_tracks(30)
    # print([track.track_number for track in tracks])

    # random shuffle
    tracks_copy = tracks.copy()
    TrackSort.sort_by_field(tracks)
    assert tracks == tracks_copy
    TrackSort.sort_by_field(tracks, reverse=True)
    assert tracks == list(reversed(tracks_copy))

    # sort on int
    for i, track in enumerate(tracks, 1):
        track.track_number = i
        track.track_total = len(tracks)

    tracks_sorted = sorted(tracks, key=lambda t: t.track_number)
    TrackSort.sort_by_field(tracks, field=TagName.TRACK)
    assert tracks == tracks_sorted
    TrackSort.sort_by_field(tracks, field=TagName.TRACK, reverse=True)
    assert tracks == list(reversed(tracks_sorted))

    # sort on datetime
    for i, track in enumerate(tracks, 1):
        track.date_modified = track.date_modified.replace(second=i)

    tracks_sorted = sorted(tracks, key=lambda t: t.date_modified)
    TrackSort.sort_by_field(tracks, field=PropertyName.DATE_MODIFIED)
    assert tracks == tracks_sorted
    TrackSort.sort_by_field(tracks, field=PropertyName.DATE_MODIFIED, reverse=True)
    assert tracks == list(reversed(tracks_sorted))

    # sort on str, ignoring defined words like 'The' and 'A'
    tracks_sorted = sorted(tracks, key=lambda t: strip_ignore_words(t.title))
    TrackSort.sort_by_field(tracks, field=TagName.TITLE)
    assert tracks == tracks_sorted
    TrackSort.sort_by_field(tracks, field=TagName.TITLE, reverse=True)
    assert tracks == list(reversed(tracks_sorted))


def test_group_by_field():
    tracks = random_tracks(30)

    assert TrackSort.group_by_field(tracks) == {None: tracks}

    groups = TrackSort.group_by_field(tracks, TagName.KEY)
    assert sorted(groups) == sorted(set(track.key for track in tracks))
    assert sum(len(t) for t in groups.values()) == len(tracks)


def test_sort():
    tracks = random_tracks(30)
    for i, track in enumerate(tracks, 1):
        track.track_number = i
        track.track_total = len(tracks)

    # random shuffle
    tracks_copy = tracks.copy()
    TrackSort().sort(tracks)
    assert tracks == tracks_copy
    TrackSort(shuffle_mode=ShuffleMode.RANDOM).sort(tracks)
    assert tracks != tracks_copy
    TrackSort(fields=TagName.TITLE, shuffle_mode=ShuffleMode.RANDOM).sort(tracks)
    assert tracks != tracks_copy

    # prepare tracks
    for track in tracks:
        track.album = choice(["album 1", "album 2"])
        track.disc_number = randrange(1, 3)

    # simple multi-sort
    tracks_sorted = sorted(tracks, key=lambda t: (t.album, t.disc_number, t.track_number))
    TrackSort(fields=[TagName.ALBUM, TagName.DISC, TagName.TRACK], shuffle_mode=ShuffleMode.NONE).sort(tracks)
    assert tracks == tracks_sorted

    # complex multi-sort, includes reverse options
    tracks_sorted = []
    sort_key_1: Callable[[LocalTrack], str] = lambda t: t.album
    for _, group_1 in groupby(sorted(tracks, key=sort_key_1, reverse=True), key=sort_key_1):
        sort_key_2: Callable[[LocalTrack], int] = lambda t: t.disc_number
        for __, group_2 in groupby(sorted(group_1, key=sort_key_2), key=sort_key_2):
            sort_key_3: Callable[[LocalTrack], int] = lambda t: t.track_number
            for ___, group_3 in groupby(sorted(group_2, key=sort_key_3, reverse=True), key=sort_key_3):
                tracks_sorted.extend(list(group_3))

    fields = {TagName.ALBUM: True, TagName.DISC: False, TagName.TRACK: True}
    TrackSort(fields=fields, shuffle_mode=ShuffleMode.NONE).sort(tracks)
    assert tracks == tracks_sorted