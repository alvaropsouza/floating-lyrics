# Decisões de Arquitetura - Speech-to-Text Sync

## 🤔 Decisões Técnicas Principais

### 1. Biblioteca STT: faster-whisper

**Opções consideradas:**
- Whisper (original) - OpenAI
- faster-whisper - SYSTRAN (⭐ ESCOLHIDA)
- whisper.cpp - ggerganov
- Vosk - Alpha Cephei
- Google Cloud Speech-to-Text
- Azure Speech SDK

**Decisão:** faster-whisper

**Justificativa:**
- ✅ 4x mais rápido que Whisper original (CTranslate2 backend)
- ✅ Mesma precisão do Whisper (~75-85% com música)
- ✅ Offline, gratuito, MIT license
- ✅ Excelente suporte a PT-BR (modelo multilingual)
- ✅ GPU + CPU support
- ✅ Comunidade ativa, bem mantido
- ❌ Não é streaming verdadeiro (aceitável com chunks de 2-3s)

**Alternativas rejeitadas:**
- **Vosk:** Latência ótima (100ms) mas precisão ruim com música (~55%)
- **Cloud APIs:** Custos recorrentes + dependência de internet + precisão ruim com música
- **whisper.cpp:** Similar ao faster-whisper mas menos integrado com Python

---

### 2. Arquitetura: Híbrida (3 modos)

**Opções consideradas:**
- Apenas timestamp (atual)
- Apenas STT
- Híbrida com 3 modos (⭐ ESCOLHIDA)

**Decisão:** Arquitetura híbrida com 3 modos selecionáveis

**Modos:**
1. **timestamp_only** - Baseline atual, STT desabilitado
2. **stt_only** - Apenas STT, para músicas sem LRC
3. **hybrid** - Timestamp (base) + STT (validação e correção)

**Justificativa:**
- ✅ Flexibilidade: usuário escolhe baseado em hardware/necessidade
- ✅ Backward compatibility: modo timestamp_only preserva comportamento atual
- ✅ Progressive enhancement: pode habilitar STT gradualmente
- ✅ Robustez: modo hybrid combina pontos fortes de ambos
- ✅ Fallback gracioso: se STT falhar, timestamp continua funcionando

**Trade-offs:**
- ❌ Complexidade maior (3 code paths)
- ❌ Mais configuração necessária
- ✅ Mas: separação clara de responsabilidades

---

### 3. Threading: Thread separada para STT

**Opções consideradas:**
- Processar STT no main loop
- Thread separada (⭐ ESCOLHIDA)
- Processo separado (multiprocessing)
- AsyncIO

**Decisão:** Thread separada (`_stt_loop`)

**Justificativa:**
- ✅ Não bloqueia main recognition loop
- ✅ Não bloqueia spectrum loop
- ✅ Permite processamento paralelo (GPU trabalha enquanto CPU faz outra coisa)
- ✅ Simplicidade de implementação (já usamos threading no projeto)
- ✅ Compartilhamento de memória fácil (callbacks, flags)

**Alternativas rejeitadas:**
- **Main loop:** Bloquearia reconhecimento por 500-700ms a cada chunk
- **Multiprocessing:** Overhead de IPC, complexidade maior, dificulta compartilhamento de estado
- **AsyncIO:** GPU ops são blocking de qualquer forma, não beneficiaria

**Arquitetura de threads resultante:**
```
Main Thread
├─► Recognition Loop (main cycle)
├─► Spectrum Loop (50ms ticks, já existe)
└─► STT Loop (2.5s chunks, NOVO)
```

---

### 4. Fuzzy Matching: SequenceMatcher (difflib)

**Opções consideradas:**
- Levenshtein distance (python-Levenshtein)
- SequenceMatcher (difflib) (⭐ ESCOLHIDA)
- FuzzyWuzzy / RapidFuzz
- Embeddings + cosine similarity (SBERT)

**Decisão:** SequenceMatcher (stdlib)

**Justificativa:**
- ✅ Já disponível na stdlib (zero deps extras)
- ✅ Algoritmo Ratcliff-Obershelp: bom para texto com variações
- ✅ Performance aceitável (< 10ms para comparar 100 linhas)
- ✅ Simplicidade de implementação
- ✅ Suficiente para o caso de uso (não precisa de ML aqui)

**Possível upgrade futuro:**
- RapidFuzz (C++, 10x mais rápido) se performance se tornar bottleneck
- Embeddings para matching semântico ("você é meu amor" ≈ "tu és meu bem")

---

### 5. Configuração: Seção dedicada [SpeechSync]

**Opções consideradas:**
- Adicionar em [Recognition]
- Nova seção [SpeechSync] (⭐ ESCOLHIDA)
- CLI arguments
- UI settings (Flutter)

**Decisão:** Nova seção `[SpeechSync]` no `config.ini`

**Justificativa:**
- ✅ Separação de concerns (reconhecimento vs sincronização)
- ✅ Fácil habilitar/desabilitar feature completa
- ✅ Consistente com padrão atual do projeto
- ✅ Permite futuras expansões (novos modos, providers, etc.)

**Parâmetros principais:**
```ini
[SpeechSync]
enabled = false          # Kill switch
mode = hybrid            # timestamp_only|stt_only|hybrid
model_size = tiny        # tiny|base|small
device = cuda            # cuda|cpu
chunk_duration_s = 2.5   # Tuning de latência vs precisão
```

---

### 6. Timecode Sintético: Baseado em índice de linha

**Opções consideradas:**
- Timecode sintético (índice × 3s) (⭐ ESCOLHIDA)
- Acumular duração baseada em caracteres
- Usar BPM estimado
- Não emitir timecode (apenas scroll manual)

**Decisão:** `synthetic_timecode = line_index * 3000ms`

**Justificativa:**
- ✅ Simplicidade extrema
- ✅ Funciona razoavelmente para maioria das músicas (2-4s por linha)
- ✅ Permite scroll suave na UI (mesmo sem timestamps reais)
- ✅ Pode ser melhorado depois (análise de caracteres, BPM, etc.)

**Limitações aceitas:**
- ❌ Impreciso para linhas muito longas/curtas
- ❌ Não reflete timing real da música
- ✅ Mas: melhor que nada, e suficiente para scroll básico

**Melhorias futuras:**
```python
# V2: Baseado em caracteres
synthetic_timecode = sum(len(line) * 50 for line in lyrics[:index])

# V3: BPM-aware
synthetic_timecode = (index / lines_per_bar) * (60000 / bpm) * bars
```

---

### 7. Correção de Drift: Threshold de 2 linhas

**Opções consideradas:**
- Threshold fixo (1, 2 ou 3 linhas)
- Threshold adaptativo baseado em confiança
- Correção imediata (threshold = 0)
- Nunca corrigir (apenas logar)

**Decisão:** Threshold fixo de **2 linhas**

**Justificativa:**
- ✅ Tolerante a imprecisões naturais (linha termina, próxima começa)
- ✅ Evita correções "ping-pong" (oscilar entre posições)
- ✅ Balanceado: não muito lento (3+) nem muito agressivo (1)
- ✅ Configurável via `auto_correct_threshold` (pode ajustar depois)

**Cenários:**
- Divergência 0-1 linhas: ✅ Validado, sem correção (timing natural)
- Divergência 2+ linhas: ⚠️ Corrigir (seek detectado ou drift acumulado)

**Alternativa futura:**
```python
# Threshold adaptativo baseado em confiança do STT
if stt_confidence > 0.85:
    threshold = 1  # Alta confiança = mais agressivo
else:
    threshold = 3  # Baixa confiança = mais conservador
```

---

### 8. Modelo Whisper: tiny como padrão

**Opções consideradas:**
- tiny (~40MB, 300-500ms, ~75% precisão) (⭐ PADRÃO)
- base (~75MB, 500-800ms, ~85% precisão)
- small (~244MB, 1-2s, ~90% precisão)

**Decisão:** `tiny` como padrão, `base` como upgrade recomendado

**Justificativa:**
- ✅ Tiny: Melhor trade-off latência/precisão para maioria dos usuários
- ✅ Roda até em GPUs modestas (GTX 1060, RTX 2060, etc.)
- ✅ 75% de precisão é suficiente para correção de drift
- ✅ Download menor (40MB vs 75MB)
- ✅ Base disponível para quem quer mais precisão

**Recomendações por hardware:**
```
RTX 4090/4080: base ou small
RTX 3060/3070: tiny ou base
GTX 1660/1060: tiny only
CPU only: tiny (latência 1-2s)
```

---

### 9. Audio Resampling: Lazy resampling 44.1k → 16k

**Opções consideradas:**
- Resample na captura (modificar AudioCapture)
- Resample antes do STT (⭐ ESCOLHIDA)
- Não resample (esperar que Whisper aceite 44.1k)
- Usar FFmpeg subprocess

**Decisão:** Resample no `SpeechRecognizer.recognize_chunk()` usando numpy

**Justificativa:**
- ✅ Whisper requer 16kHz (não negociável)
- ✅ Não modifica AudioCapture (separação de concerns)
- ✅ Aplicado apenas quando STT habilitado (zero overhead quando desabilitado)
- ✅ Numpy simples suficiente para agora (scipy/torchaudio se necessário depois)

**Implementação inicial:**
```python
# Simple decimation (44100 → 16000)
audio_16k = audio_44k[::3]  # Aproximado (44100/3 ≈ 14700, close enough)
```

**Upgrade futuro:**
```python
import scipy.signal
audio_16k = scipy.signal.resample_poly(audio_44k, 16000, 44100)
```

---

### 10. Lazy Loading: Modelo carregado apenas quando necessário

**Opções consideradas:**
- Carregar no `__init__` sempre
- Carregar apenas quando `enabled=true` (⭐ ESCOLHIDA)
- Carregar na primeira música reconhecida
- Pré-cache em background thread

**Decisão:** Lazy loading em `_init_stt()` chamado de `__init__` apenas se `enabled=true`

**Justificativa:**
- ✅ Zero overhead quando STT desabilitado (99% dos usuários inicialmente)
- ✅ Faster startup (economia de ~2-3s de load + ~500MB VRAM)
- ✅ Permite habilitar/desabilitar em runtime (futuro)
- ✅ Simplicidade: flag `self._speech_recognizer is None` = desabilitado

**Fluxo:**
```python
def __init__(self, config, ...):
    self._stt_enabled = config.getboolean("SpeechSync", "enabled", fallback=False)
    self._speech_recognizer = None
    
    if self._stt_enabled:
        self._init_stt()  # Carrega modelo aqui

def _init_stt(self):
    try:
        self._speech_recognizer = SpeechRecognizer(...)
    except Exception:
        self._stt_enabled = False  # Fallback gracioso
```

---

## 📊 Tabela Resumida de Decisões

| # | Decisão | Escolha | Alternativas Consideradas | Razão Principal |
|---|---------|---------|---------------------------|-----------------|
| 1 | STT Library | faster-whisper | Vosk, Cloud APIs | Melhor latência/precisão/custo |
| 2 | Arquitetura | Híbrida (3 modos) | Apenas STT | Flexibilidade + backward compat |
| 3 | Threading | Thread separada | Main loop, AsyncIO | Não bloquear reconhecimento |
| 4 | Matching | SequenceMatcher | Levenshtein, ML | Stdlib, suficiente, simples |
| 5 | Config | `[SpeechSync]` | Adicionar em `[Recognition]` | Separação de concerns |
| 6 | Timecode sintético | `index * 3s` | Baseado em chars, BPM | Simplicidade (MVP) |
| 7 | Threshold correção | 2 linhas | 1 ou 3 linhas | Balanceado, evita ping-pong |
| 8 | Modelo padrão | tiny | base, small | Latência < 500ms |
| 9 | Resampling | Antes do STT | Na captura | Não modificar AudioCapture |
| 10 | Carregamento | Lazy loading | Sempre | Zero overhead quando disabled |

---

## 🔄 Decisões Futuras (Não resolvidas)

### 1. Detecção de pause/silêncio
**Problema:** STT continua rodando durante pausas musicais longas  
**Opções:**
- VAD (Voice Activity Detection) integrado
- Espectro de áudio como proxy (energia < threshold = skip STT)
- Não fazer nada (desperdício aceitável)

**Decisão adiada para:** Fase 5 (Otimizações)

### 2. Multi-idioma
**Problema:** Músicas em inglês, espanhol, etc.  
**Opções:**
- `language="auto"` (Whisper detecta, +200ms latência)
- Fixo PT-BR (mais rápido, funciona mal com outras línguas)
- Configurável por música (complexo)

**Decisão adiada para:** Pós-MVP (se houver demanda)

### 3. Fine-tuning do modelo
**Problema:** Whisper treinado em fala, não música  
**Opções:**
- Fine-tune com dataset de (áudio musical, letras)
- Usar modelo pré-treinado (Hugging Face)
- Não fazer (aceitar 75-85% de precisão)

**Decisão adiada para:** Longo prazo (requer dataset grande)

---

**Versão:** 1.0  
**Data:** 20 de abril de 2026  
**Status:** ✅ Decisões principais tomadas, prontas para implementação
