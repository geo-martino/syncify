"""
Configuration relating to the :py:mod:`Spotify` module.

This configuration can be used to inject dependencies into dependencies throughout the module.
"""
from dataclasses import dataclass

from musify.shared.remote.factory import RemoteObjectFactory
from musify.spotify.object import SpotifyPlaylist, SpotifyTrack, SpotifyAlbum, SpotifyArtist


@dataclass
class SpotifyObjectFactory(RemoteObjectFactory):
    playlist: type[SpotifyPlaylist] = SpotifyPlaylist
    track: type[SpotifyTrack] = SpotifyTrack
    album: type[SpotifyAlbum] = SpotifyAlbum
    artist: type[SpotifyArtist] = SpotifyArtist
