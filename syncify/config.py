from collections.abc import Mapping

from syncify.remote.config import RemoteClasses
from syncify.spotify import SPOTIFY_SOURCE_NAME
from syncify.spotify.api import SpotifyAPI
from syncify.spotify.base import SpotifyObject
from syncify.spotify.library.library import SpotifyLibrary
from syncify.spotify.processors.processors import SpotifyItemChecker, SpotifyItemSearcher
from syncify.spotify.processors.wrangle import SpotifyDataWrangler

# map of the names of all supported remote sources and their associated implementations
REMOTE_CONFIG: Mapping[str, RemoteClasses] = {
    SPOTIFY_SOURCE_NAME.casefold().strip(): RemoteClasses(
        name=SPOTIFY_SOURCE_NAME,
        api=SpotifyAPI,
        wrangler=SpotifyDataWrangler,
        object=SpotifyObject,
        library=SpotifyLibrary,
        checker=SpotifyItemChecker,
        searcher=SpotifyItemSearcher,
    )
}
