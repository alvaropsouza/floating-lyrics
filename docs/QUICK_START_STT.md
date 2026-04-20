# Quick Start - Implementação STT

## 🚀 Começar Agora (Fase 1)

### 1. Instalar Dependências (5 min)

```bash
# Ativar ambiente virtual
cd c:\Users\alvar\Documents\projects\floating-lyrics
.\.venv\Scripts\activate

# Instalar faster-whisper
pip install faster-whisper==1.1.0 ctranslate2==4.5.0

# Verificar instalação
python -c "from faster_whisper import WhisperModel; print('✓ faster-whisper OK')"

# Baixar modelo tiny (primeira execução baixa automaticamente)
python -c "from faster_whisper import WhisperModel; m = WhisperModel('tiny'); print('✓ Modelo tiny baixado')"
```

**Tempo:** ~5-10 minutos (depende da velocidade de download)  
**Tamanho:** ~74 MB (modelo multilingual tiny)

---

### 2. Criar `src/speech_recognition.py` (30-60 min)

**Arquivo:** `c:\Users\alvar\Documents\projects\floating-lyrics\src\speech_recognition.py`

**Código inicial:**

```python
"""
Speech-to-Text para sincronização de letras musicais.
"""

from __future__ import annotations

import logging
import numpy as np
from dataclasses import dataclass
from typing import Optional

# Lazy import (só carrega se usado)
_whisper_model = None

_LOG = logging.getLogger(__name__)


@dataclass
class SpeechSegment:
    """Segmento de texto reconhecido."""
    text: str
    confidence: float  # -1.0 a 0.0 (avg_logprob do Whisper)
    start_ms: int
    end_ms: int


class SpeechRecognizer:
    """Reconhecedor de voz otimizado para música usando Whisper."""
    
    def __init__(self, model_size: str = "tiny", device: str = "cuda"):
        """
        Args:
            model_size: "tiny", "base", "small" (tiny recomendado)
            device: "cuda" (GPU) ou "cpu"
        """
        try:
            from faster_whisper import WhisperModel
            
            _LOG.info(f"Carregando Whisper model={model_size}, device={device}")
            self.model = WhisperModel(
                model_size, 
                device=device,
                compute_type="int8"  # Otimizado para latência
            )
            self.sample_rate = 16000  # Whisper requer 16kHz
            _LOG.info("Whisper carregado com sucesso")
            
        except Exception as exc:
            _LOG.error(f"Erro ao carregar Whisper: {exc}", exc_info=True)
            raise
    
    def recognize_chunk(
        self, 
        audio_data: bytes, 
        sample_rate: int = 44100,
        duration_s: float = 3.0
    ) -> Optional[SpeechSegment]:
        """
        Reconhece voz em um chunk de áudio.
        
        Args:
            audio_data: Áudio PCM int16 mono
            sample_rate: Taxa de amostragem original (44100 padrão)
            duration_s: Duração do chunk
            
        Returns:
            SpeechSegment ou None se sem voz
        """
        try:
            # Converter bytes → numpy array float32
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_float = audio_array.astype(np.float32) / 32768.0
            
            # Resample 44.1kHz → 16kHz se necessário
            if sample_rate != self.sample_rate:
                audio_float = self._simple_resample(
                    audio_float, 
                    sample_rate, 
                    self.sample_rate
                )
            
            # Transcrever
            segments, info = self.model.transcribe(
                audio_float,
                language="pt",
                beam_size=1,      # Beam=1 para latência mínima
                best_of=1,
                temperature=0.0,  # Greedy decoding
                vad_filter=True,  # Voice Activity Detection
                vad_parameters=dict(
                    threshold=0.5,
                    min_speech_duration_ms=250
                )
            )
            
            # Pegar primeiro segmento
            for segment in segments:
                if segment.text.strip():
                    return SpeechSegment(
                        text=segment.text.strip(),
                        confidence=segment.avg_logprob,
                        start_ms=int(segment.start * 1000),
                        end_ms=int(segment.end * 1000)
                    )
            
            return None
            
        except Exception as exc:
            _LOG.error(f"Erro ao reconhecer áudio: {exc}", exc_info=True)
            return None
    
    @staticmethod
    def _simple_resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """Resample simples por decimação (versão rápida, não ideal)."""
        if orig_sr == target_sr:
            return audio
        
        # Decimação simples (pegar 1 a cada N samples)
        step = orig_sr // target_sr
        return audio[::step]
```

**Teste rápido:**

```python
# test_stt.py
from src.speech_recognition import SpeechRecognizer
import wave
import numpy as np

# Criar recognizer
rec = SpeechRecognizer(model_size="tiny", device="cuda")

# Gerar áudio de teste (silêncio)
audio = np.zeros(16000 * 3, dtype=np.int16)  # 3s de silêncio
audio_bytes = audio.tobytes()

# Reconhecer
segment = rec.recognize_chunk(audio_bytes, sample_rate=16000)
print(f"Resultado: {segment}")  # Deve ser None (sem voz)
```

---

### 3. Criar `src/lyrics_matcher.py` (30-60 min)

**Arquivo:** `c:\Users\alvar\Documents\projects\floating-lyrics\src\lyrics_matcher.py`

**Código inicial:**

```python
"""
Fuzzy matching de texto reconhecido com letras.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional


@dataclass
class LyricMatch:
    """Resultado de matching."""
    line_index: int
    similarity: float
    matched_text: str
    recognized_text: str


class LyricsMatcher:
    """Encontra posição na letra baseado em texto reconhecido."""
    
    def __init__(self, min_similarity: float = 0.6):
        self.min_similarity = min_similarity
        self._cached_lyrics: List[str] = []
        self._normalized_lyrics: List[str] = []
    
    def set_lyrics(self, lyrics: List[str]) -> None:
        """Define letra da música atual."""
        self._cached_lyrics = lyrics
        self._normalized_lyrics = [
            self._normalize_text(line) for line in lyrics
        ]
    
    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normaliza texto: remove acentos, pontuação, lowercase."""
        # Remover acentos
        text = unicodedata.normalize('NFKD', text)
        text = text.encode('ascii', 'ignore').decode('ascii')
        
        # Lowercase + remover pontuação
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        
        # Normalizar espaços
        return ' '.join(text.split())
    
    def find_best_match(
        self, 
        recognized_text: str,
        context_window: int = 10,
        current_index: int = 0
    ) -> Optional[LyricMatch]:
        """
        Encontra melhor match na letra.
        
        Args:
            recognized_text: Texto do STT
            context_window: Linhas para buscar antes/depois
            current_index: Posição atual (otimização)
            
        Returns:
            LyricMatch ou None
        """
        if not self._normalized_lyrics or not recognized_text:
            return None
        
        normalized_input = self._normalize_text(recognized_text)
        best_match: Optional[LyricMatch] = None
        best_similarity = 0.0
        
        # Buscar próximo da posição atual primeiro
        start = max(0, current_index - context_window)
        end = min(len(self._normalized_lyrics), current_index + context_window)
        
        for i in range(start, end):
            sim = SequenceMatcher(
                None, 
                normalized_input, 
                self._normalized_lyrics[i]
            ).ratio()
            
            if sim > best_similarity:
                best_similarity = sim
                best_match = LyricMatch(
                    line_index=i,
                    similarity=sim,
                    matched_text=self._cached_lyrics[i],
                    recognized_text=recognized_text
                )
        
        # Se não achou, buscar no resto
        if best_similarity < self.min_similarity:
            for i in range(len(self._normalized_lyrics)):
                if start <= i < end:
                    continue
                
                sim = SequenceMatcher(
                    None, 
                    normalized_input, 
                    self._normalized_lyrics[i]
                ).ratio()
                
                if sim > best_similarity:
                    best_similarity = sim
                    best_match = LyricMatch(
                        line_index=i,
                        similarity=sim,
                        matched_text=self._cached_lyrics[i],
                        recognized_text=recognized_text
                    )
        
        if best_match and best_match.similarity >= self.min_similarity:
            return best_match
        
        return None
```

**Teste rápido:**

```python
# test_matcher.py
from src.lyrics_matcher import LyricsMatcher

matcher = LyricsMatcher(min_similarity=0.6)

# Letra de exemplo
lyrics = [
    "Primeira linha da música",
    "Segunda linha cantada aqui",
    "Terceira linha do refrão"
]
matcher.set_lyrics(lyrics)

# Testar match
result = matcher.find_best_match("segunda linha cantada", current_index=0)
print(f"Match: linha {result.line_index}, similaridade {result.similarity:.2f}")
# Deve mostrar: linha 1, similaridade ~0.95
```

---

### 4. Adicionar Configuração (5 min)

**Arquivo:** `c:\Users\alvar\Documents\projects\floating-lyrics\config.ini`

Adicionar no final do arquivo:

```ini
[SpeechSync]
# Reconhecimento de voz para sincronização de letras
enabled = false
mode = hybrid
model_size = tiny
device = cuda
chunk_duration_s = 2.5
validation_interval_ms = 5000
min_similarity = 0.65
auto_correct_threshold = 2
```

---

### 5. Teste Manual Completo (10 min)

**Script de teste:** `scripts/test_stt_manual.py`

```python
"""
Teste manual de STT com áudio real.
"""

import logging
from pathlib import Path
from src.speech_recognition import SpeechRecognizer
from src.lyrics_matcher import LyricsMatcher

logging.basicConfig(level=logging.INFO)

def test_stt():
    print("🎤 Teste de Speech Recognition")
    
    # Inicializar
    rec = SpeechRecognizer(model_size="tiny", device="cuda")
    matcher = LyricsMatcher(min_similarity=0.6)
    
    # Letra de exemplo
    lyrics = [
        "Quando a luz dos olhos meus",
        "E a luz dos olhos teus",
        "Resolvem se encontrar",
        "Ai que bom que isso é meu Deus",
    ]
    matcher.set_lyrics(lyrics)
    
    # Testar com áudio de debug (se existir)
    debug_dir = Path("cache/debug_audio")
    if debug_dir.exists():
        wav_files = list(debug_dir.glob("*.wav"))
        if wav_files:
            print(f"📁 Encontrados {len(wav_files)} arquivos de debug")
            # TODO: Carregar WAV e processar
        else:
            print("⚠️ Sem arquivos WAV em cache/debug_audio")
    else:
        print("⚠️ Diretório cache/debug_audio não existe")
    
    print("✅ Teste concluído")

if __name__ == "__main__":
    test_stt()
```

---

## 📋 Checklist Fase 1

- [ ] Dependências instaladas (`faster-whisper`, `ctranslate2`)
- [ ] Modelo tiny baixado (~74MB)
- [ ] `src/speech_recognition.py` criado e testado
- [ ] `src/lyrics_matcher.py` criado e testado
- [ ] Configuração adicionada em `config.ini`
- [ ] Testes manuais executados com sucesso

**Próximo passo:** Fase 2 - Integração com `worker_headless.py`

---

## 🔧 Troubleshooting

### Erro: "Could not load library cudnn_cnn_infer64_8.dll"

**Solução:** CUDA/cuDNN não instalado corretamente

```bash
# Opção 1: Instalar CUDA Toolkit
# https://developer.nvidia.com/cuda-downloads

# Opção 2: Usar CPU
rec = SpeechRecognizer(model_size="tiny", device="cpu")
```

### Erro: "No module named 'faster_whisper'"

**Solução:**

```bash
pip install faster-whisper==1.1.0 ctranslate2==4.5.0
```

### Latência muito alta (>2s)

**Causas possíveis:**
- Rodando em CPU (esperado: 1-2s)
- Modelo muito grande (tiny é o mais rápido)
- GPU fraca

**Soluções:**
- Usar GPU dedicada (NVIDIA RTX)
- Reduzir chunk_duration_s para 2.0s
- Verificar device: `device="cuda"` vs `device="cpu"`

### Precisão ruim (<50%)

**Causas possíveis:**
- Áudio com muito ruído/efeitos
- Voz muito baixa no mix
- Idioma errado

**Soluções:**
- Testar com músicas vocalmente claras primeiro
- Aumentar chunk_duration_s para 3.5s
- Usar modelo base (mais preciso, mais lento)

---

**Tempo total Fase 1:** ~2-3 horas  
**Próxima leitura:** [IMPLEMENTATION_PLAN_STT.md](./IMPLEMENTATION_PLAN_STT.md) - Fase 2
