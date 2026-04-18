# Debug de Áudio - Salvando Captures para Análise

## 📊 O que é?
Quando habilitado, todos os áudios enviados para as APIs de reconhecimento (AudD e ACRCloud) são salvos em disco para análise posterior.

## 📂 Onde os arquivos são salvados?
```
cache/
  └─ debug_audio/
      ├─ 20260418_143025_audd.wav      (enviado para AudD)
      ├─ 20260418_143027_acrcloud.wav  (enviado para ACRCloud)
      ├─ 20260418_143031_audd.wav
      └─ ...
```

**Padrão de nome**: `YYYYMMDD_HHMMSS_<provider>.wav`

## ⚙️ Como ativar?

### Opção 1: Via config.ini (recomendado)
```ini
[Recognition]
save_audio_for_debug = true
```

### Opção 2: Via código (dev)
```python
from src.song_recognition import _save_debug_audio

# Salvar manual
audio_bytes = …  # seu áudio WAV
_save_debug_audio(audio_bytes, provider_name="custom")
```

## 🎯 Casos de uso

### 1. **Debug de Reconhecimento**
Reveja o áudio enviado para entender por que uma música foi/não foi reconhecida:
```bash
# Ouvir o arquivo salvo
ffplay cache/debug_audio/20260418_143025_audd.wav
```

### 2. **Testes Offline**
Use áudios capturados para testar alterações sem fazer requisições à API:
```python
with open("cache/debug_audio/20260418_143025_audd.wav", "rb") as f:
    audio_bytes = f.read()
    result, _ = recognizer.recognize(audio_bytes, time.perf_counter())
```

### 3. **Análise de Qualidade de Capture**
- Verificar se o áudio capturado tem boa qualidade
- Confirmar duração e taxa de amostragem
- Detectar problemas de latência ou silêncio

### 4. **Treinar LLM Local**
Use os áudios como dataset para fine-tuning do llm-music-api:
```
llm-music-api/
  └─ training_data/
      └─ cache/debug_audio/*.wav  ← usar esses
```

## ⚠️ Cuidados

### Uso de Disco
- Cada áudio: ~5-10 MB (5 segundos a 44.1kHz)
- Limite manual: limpar periodicamente
  ```bash
  rm -rf cache/debug_audio/*     # Limpar pasta
  rm cache/debug_audio/*audd*.wav # Limpar específico
  ```

### Privacidade
- **Desativar após debug!** (padrão já é `false`)
- Áudios capturados podem conter áudio privado (ex: voz)
- Não commitar `cache/debug_audio/` no git

### Performance
- **Impacto mínimo**: salvamento não bloqueia thread principal
- Novo diretório criado automaticamente
- Log de debug indicando arquivo salvo

## 📊 Exemplo de Logs

Com `save_audio_for_debug = true`:

```
[DEBUG] 🎵 Áudio salvo para debug: cache/debug_audio/20260418_143025_audd.wav
[INFO] Fallback de reconhecimento iniciado | ordem=acrcloud → audd | tentativas_por_provedor=2
[DEBUG] 🎵 Áudio salvo para debug: cache/debug_audio/20260418_143027_acrcloud.wav
[INFO] Nova música: Song Title - Artist Name
```

## 🔧 Formato dos Arquivos

Todas os arquivos salvos estão no formato **WAV**:
- **Codec**: PCM (não comprimido)
- **Taxa**: 44.1 kHz (44.1k samples/s)
- **Canais**: 1 (mono)
- **Duração**: 5 segundos (típico)

Ferramentas para análise:
```bash
# Audacity (GUI)
audacity cache/debug_audio/20260418_143025_audd.wav

# ffmpeg (CLI)
ffprobe cache/debug_audio/20260418_143025_audd.wav
ffplay cache/debug_audio/20260418_143025_audd.wav
```

## 🚀 Próximos Passos

1. **Ativar para debug**: `save_audio_for_debug = true`
2. **Usar uma música**: deixar rodar o reconhecimento normalmente
3. **Verificar pasta**: `cache/debug_audio/` terá os WAVs
4. **Analisar**: ouvir em Audacity ou usar ffprobe
5. **Desativar**: `save_audio_for_debug = false` (importante!)

## 📝 Código Técnico

Implementação em `src/song_recognition.py`:

```python
def _save_debug_audio(audio_bytes: bytes, provider_name: str = "unknown") -> None:
    """Salva áudio capturado em arquivo para debug."""
    try:
        from src.config import Config
        config = Config()
        save_debug = config.getboolean("Recognition", "save_audio_for_debug", fallback=False)
        
        if not save_debug:
            return
        
        _DEBUG_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        
        timestamp = _time.strftime("%Y%m%d_%H%M%S")
        filename = _DEBUG_AUDIO_DIR / f"{timestamp}_{provider_name}.wav"
        
        with open(filename, "wb") as f:
            f.write(audio_bytes)
        
        _LOG.debug(f"🎵 Áudio salvo para debug: {filename.relative_to(_CACHE_DIR.parent)}")
        
    except Exception as exc:
        _LOG.warning(f"Não foi possível salvar áudio para debug: {exc}")
```

Chamado em:
- ✅ `AudDRecognizer.recognize()` → `_save_debug_audio(audio_bytes, "audd")`
- ✅ `ACRCloudRecognizer.recognize()` → `_save_debug_audio(audio_bytes, "acrcloud")`

---

**Status**: ✅ Implementado
**Versão**: 1.0
**Data**: 2026-04-18
