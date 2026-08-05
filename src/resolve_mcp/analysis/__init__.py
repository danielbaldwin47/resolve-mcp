"""What the agent hears in the audio: beats, downbeats, energy.

Nothing heavy is imported here. ``decode`` and ``energy`` pull numpy and scipy, and the beat
model pulls torch, so the workers import what they need when they run and server startup
stays a stdio handshake rather than a scientific stack (see ``jobs.runner``).
"""

from __future__ import annotations
