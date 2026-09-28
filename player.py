"""Backward-compatible facade; real queue implementation is music_player.py."""
from music_player import MusicPlayer, PlayerService as _PlayerService, Track


class PlayerService(_PlayerService):
    """Compatibility subclass preserving the original public class name."""
    pass


__all__ = ["MusicPlayer", "PlayerService", "Track"]
