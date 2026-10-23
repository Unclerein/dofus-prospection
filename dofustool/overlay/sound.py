"""Petit son discret qui accompagne un bandeau : deux notes douces, courtes, à faible volume.

Le son est fabriqué ici (pas de fichier livré) et joué par Windows sans bloquer la fenêtre.
"""
import math
import struct
import sys
import wave
from pathlib import Path

RATE = 44_100
NOTES = ((880.0, 0.0), (1318.5, 0.11))  # la puis mi, la seconde un peu après la première
LENGTH_S = 0.55
VOLUME = 0.16  # discret : bien en dessous du son du jeu


def chime() -> bytes:
    """Échantillons 16 bits : chaque note attaque doucement puis s'éteint, avec une harmonique légère."""
    frames = []
    for i in range(int(RATE * LENGTH_S)):
        t = i / RATE
        value = 0.0
        for frequency, start in NOTES:
            local = t - start
            if local < 0:
                continue
            envelope = min(1.0, local / 0.012) * math.exp(-local * 7.0)
            value += envelope * (math.sin(2 * math.pi * frequency * local) + 0.18 * math.sin(4 * math.pi * frequency * local))
        frames.append(struct.pack("<h", round(max(-1.0, min(1.0, value * VOLUME)) * 32_000)))
    return b"".join(frames)


def ensure(path: Path) -> Path:
    """Écrit le son une fois dans ce fichier (WAV mono)."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(chime())
    return path


def play(path: Path) -> None:
    """Joue le son sans attendre ; ne fait rien hors de Windows ou si le son est indisponible."""
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.PlaySound(str(ensure(path)), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except (OSError, RuntimeError):
        pass
