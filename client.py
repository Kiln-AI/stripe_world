# Required by `openenv push`. The typed client for every Seahaven world lives in
# seahaven; this world's client is it.
from seahaven.openenv import SeahavenClient as Client

__all__ = ["Client"]
