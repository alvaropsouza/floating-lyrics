#!/usr/bin/env python3
"""Treino e inferencia por similaridade acustica para identificacao de musica.

Estratégia:
- Extrai embedding simples de audio (mel + mfcc + chroma, mean/std)
- Treina index local (matriz de features + metadados)
- Identifica por similaridade cosseno (top-k)
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import librosa
import soundfile as sf


AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}


@dataclass
class MatchResult:
    title: str
    artist: str
    album: str
    confidence: float
    similarity: float


class AudioMatcher:
    def __init__(
        self,
        sample_rate: int = 22050,
        n_mels: int = 64,
        n_mfcc: int = 20,
        index_path: str = "/app/models/audio_index.npz",
    ) -> None:
        self.sample_rate = sample_rate
        self.n_mels = n_mels
        self.n_mfcc = n_mfcc
        self.index_path = index_path

        self._features: Optional[np.ndarray] = None
        self._metadata: List[Dict[str, Any]] = []

        self._try_load_index()

    @property
    def ready(self) -> bool:
        return self._features is not None and len(self._metadata) > 0

    def _try_load_index(self) -> None:
        if not os.path.exists(self.index_path):
            return

        payload = np.load(self.index_path, allow_pickle=True)
        self._features = payload["features"]
        metadata_json = str(payload["metadata_json"])
        self._metadata = json.loads(metadata_json)

    def _save_index(self) -> None:
        if self._features is None:
            return

        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        np.savez_compressed(
            self.index_path,
            features=self._features,
            metadata_json=json.dumps(self._metadata, ensure_ascii=False),
        )

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        return str(Path(file_path).resolve())

    def _file_state(self, file_path: str) -> Dict[str, Any]:
        stat = os.stat(file_path)
        return {
            "path": self._normalize_path(file_path),
            "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            "size": int(stat.st_size),
        }

    @staticmethod
    def _same_file_state(meta: Dict[str, Any], file_state: Dict[str, Any]) -> bool:
        return (
            meta.get("path") == file_state["path"]
            and int(meta.get("mtime_ns", -1)) == file_state["mtime_ns"]
            and int(meta.get("size", -1)) == file_state["size"]
        )

    def _parse_meta_from_path(self, file_path: str) -> Dict[str, str]:
        # Formato legado: artist__title__album.ext
        name = os.path.splitext(os.path.basename(file_path))[0]
        parts = name.split("__")

        if len(parts) >= 3:
            artist = parts[0].replace("-", " ").strip()
            title = parts[1].replace("-", " ").strip()
            album = parts[2].replace("-", " ").strip() if len(parts) > 2 else ""
            return {"artist": artist, "title": title, "album": album}

        # Formato recomendado:
        # training_audio/{artist}/{album}/{title}__{timestamp}.wav
        parent = os.path.basename(os.path.dirname(file_path)).strip()
        grandparent = os.path.basename(os.path.dirname(os.path.dirname(file_path))).strip()
        if grandparent and parent:
            title = parts[0].replace("-", " ").strip() if parts else name.strip()
            artist = grandparent.replace("-", " ").strip()
            album = parent.replace("-", " ").strip()
            return {"artist": artist, "title": title, "album": album}

        # Compatibilidade: artist__title.ext (sem album)
        if len(parts) >= 2:
            artist = parts[0].replace("-", " ").strip()
            title = parts[1].replace("-", " ").strip()
            return {"artist": artist, "title": title, "album": ""}

        # Fallback final
        return {"artist": parent or "Unknown", "title": name.strip(), "album": ""}

    def _to_mono_float32(self, signal: np.ndarray) -> np.ndarray:
        if signal.ndim > 1:
            signal = np.mean(signal, axis=1)
        signal = signal.astype(np.float32)
        peak = np.max(np.abs(signal)) if signal.size else 0.0
        if peak > 0:
            signal = signal / peak
        return signal

    def _extract_embedding(self, signal: np.ndarray, sr: int) -> np.ndarray:
        if sr != self.sample_rate:
            signal = librosa.resample(signal, orig_sr=sr, target_sr=self.sample_rate)
            sr = self.sample_rate

        # Mel spectrogram
        mel = librosa.feature.melspectrogram(y=signal, sr=sr, n_mels=self.n_mels)
        mel_db = librosa.power_to_db(mel, ref=np.max)

        # MFCC
        mfcc = librosa.feature.mfcc(y=signal, sr=sr, n_mfcc=self.n_mfcc)

        # Chroma
        chroma = librosa.feature.chroma_stft(y=signal, sr=sr)

        # Spectral contrast (captura diferenças de timbre)
        contrast = librosa.feature.spectral_contrast(y=signal, sr=sr)

        # Tonnetz (representação harmônica mais detalhada)
        tonnetz = librosa.feature.tonnetz(y=signal, sr=sr)

        # Zero crossing rate (características rítmicas)
        zcr = librosa.feature.zero_crossing_rate(y=signal)

        # Segmentação temporal: dividir em 8 janelas para capturar evolução temporal
        n_segments = 8
        feats: List[np.ndarray] = []
        
        for block_name, block in [("mel", mel_db), ("mfcc", mfcc), ("chroma", chroma), 
                                    ("contrast", contrast), ("tonnetz", tonnetz), ("zcr", zcr)]:
            n_frames = block.shape[1]
            segment_size = max(1, n_frames // n_segments)
            
            # Estatísticas globais (para compatibilidade)
            feats.append(np.mean(block, axis=1))
            feats.append(np.std(block, axis=1))
            
            # Estatísticas por segmento temporal (captura evolução)
            for i in range(n_segments):
                start = i * segment_size
                end = min((i + 1) * segment_size, n_frames)
                if end > start:
                    segment = block[:, start:end]
                    feats.append(np.mean(segment, axis=1))

        emb = np.concatenate(feats).astype(np.float32)
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb /= norm
        return emb

    def _load_audio_file(self, file_path: str) -> Tuple[np.ndarray, int]:
        signal, sr = librosa.load(file_path, sr=None, mono=False)
        signal = self._to_mono_float32(np.asarray(signal).T if signal.ndim > 1 else signal)
        return signal, sr

    def _load_audio_bytes(self, audio_bytes: bytes) -> Tuple[np.ndarray, int]:
        signal, sr = sf.read(io.BytesIO(audio_bytes), always_2d=False)
        signal = self._to_mono_float32(np.asarray(signal))
        return signal, sr

    def train_from_folder(self, dataset_path: str) -> Dict[str, Any]:
        if not os.path.isdir(dataset_path):
            raise ValueError(f"dataset_path nao existe: {dataset_path}")

        dataset_root = self._normalize_path(dataset_path)
        vectors: List[np.ndarray] = []
        metadata: List[Dict[str, Any]] = []
        skipped = 0
        added = 0
        updated = 0
        unchanged = 0
        removed = 0

        existing_by_path: Dict[str, Tuple[np.ndarray, Dict[str, Any]]] = {}
        if self.ready and self._features is not None:
            for idx, meta in enumerate(self._metadata):
                path = meta.get("path")
                if not path:
                    continue
                existing_by_path[self._normalize_path(path)] = (
                    self._features[idx],
                    meta,
                )

        current_paths: set[str] = set()

        for root, _dirs, files in os.walk(dataset_path):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue

                file_path = os.path.join(root, name)
                normalized_path = self._normalize_path(file_path)
                current_paths.add(normalized_path)
                try:
                    file_state = self._file_state(file_path)
                    existing = existing_by_path.get(normalized_path)
                    if existing is not None:
                        existing_emb, existing_meta = existing
                        if self._same_file_state(existing_meta, file_state):
                            vectors.append(existing_emb)
                            metadata.append(existing_meta)
                            unchanged += 1
                            continue

                    signal, sr = self._load_audio_file(file_path)
                    emb = self._extract_embedding(signal, sr)
                    meta = self._parse_meta_from_path(file_path)
                    meta.update(file_state)

                    vectors.append(emb)
                    metadata.append(meta)
                    if existing is None:
                        added += 1
                    else:
                        updated += 1
                except Exception:
                    skipped += 1

        if not vectors:
            raise ValueError("Nenhum audio valido encontrado para treino")

        removed = sum(1 for path in existing_by_path if path not in current_paths)

        self._features = np.vstack(vectors).astype(np.float32)
        self._metadata = metadata
        self._save_index()

        return {
            "trained": True,
            "items": len(metadata),
            "skipped": skipped,
            "added": added,
            "updated": updated,
            "removed": removed,
            "unchanged": unchanged,
            "mode": "incremental",
            "dataset_path": dataset_root,
            "index_path": self.index_path,
        }

    def identify_audio_bytes(self, audio_bytes: bytes, top_k: int = 3) -> Dict[str, Any]:
        if not self.ready:
            raise RuntimeError("Index de audio nao treinado. Chame /train-index primeiro.")

        signal, sr = self._load_audio_bytes(audio_bytes)
        query = self._extract_embedding(signal, sr)

        sims = self._features @ query
        order = np.argsort(sims)[::-1][: max(1, top_k)]

        matches: List[MatchResult] = []
        for idx in order:
            sim = float(sims[idx])
            confidence = float(max(0.0, min(1.0, (sim + 1.0) / 2.0)))
            meta = self._metadata[int(idx)]
            matches.append(
                MatchResult(
                    title=meta.get("title", "Unknown"),
                    artist=meta.get("artist", "Unknown"),
                    album=meta.get("album", ""),
                    confidence=confidence,
                    similarity=sim,
                )
            )

        best = matches[0]
        
        # Log detalhado para debug de reconhecimento
        print(f"[audio_matcher] Top {len(matches)} matches:", flush=True)
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m.title} - {m.artist} | sim={m.similarity:.6f} conf={m.confidence:.4f}", flush=True)
        if len(matches) > 1:
            sim_diff = matches[0].similarity - matches[1].similarity
            print(f"[audio_matcher] Diferença 1º-2º: {sim_diff:.6f}", flush=True)
        
        return {
            "song": best.title,
            "artist": best.artist,
            "album": best.album,
            "confidence": best.confidence,
            "method": "audio_similarity",
            "top_matches": [m.__dict__ for m in matches],
        }

    def identify_audio_base64(self, audio_base64: str, top_k: int = 3) -> Dict[str, Any]:
        try:
            audio_bytes = base64.b64decode(audio_base64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"audio_base64 invalido: {exc}")
        return self.identify_audio_bytes(audio_bytes, top_k=top_k)
