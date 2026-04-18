# Melhorias no Algoritmo de Detecção de Mudança de Música

## Problem Statement
O algoritmo anterior tinha dificuldade em detectar mudanças de música rapidamente:
- Dependia principalmente de silêncio prolongado (~3s)
- Intervalo de tracking de 30s era muito longo
- Sem análise de confiança para detectar reconhecimentos incorretos
- Crossfades e mudanças bruscas de volume não eram detectados

## Solução Implementada: 4 Camadas de Detecção

### 1. **Detecção por Intervalo Responsivo** (5-15 segundos)
- Reduzido de 30s para máximo 15s
- Em tracking mode: reconhecer a cada ~(duração × 0.85) s, máximo 15s
- Se confiança for baixa: reconhecer a cada 2s
- Em reconhecimento normal: intervalo de 5s

```python
# _time_until_next_check()
tracking_interval = min(
    self._current_song_duration_s * 0.85,  # Esperar até perto do fim
    15.0  # Mas não mais de 15s
)
```

### 2. **Detecção por Confiança Baixa** (<50%)
Rastreia reconhecimentos com baixa confiança consecutivos:
- Se `confidence < 0.5`: incrementar contador
- Se `confidence >= 0.5`: resetar contador
- Se 2+ em sequência: assumir mudança de música

**Benefício**: Detecta reconhecimentos incorretos ou ambíguos (ex: áudio em crossfade)

```python
if self._low_confidence_count >= self._low_confidence_trigger:
    _LOG.warning("2+ reconhecimentos com confiança baixa → mudar música")
    reset_current_song()
```

### 3. **Detecção por Mudança de Espectro** (Energia Audio)
Compara energia média entre frames consecutivos:
- Manter histórico de últimas 20 frames (~1s)
- Se energia cair > 40% E ficar baixa (<0.3): detectar mudança
- Útil para crossfades ou mudanças abruptas de volume

**Benefício**: Detecta mudanças mesmo sem silêncio

```python
energy_change = abs(avg - prev) / (prev + 0.001)
if energy_change > 0.4 and avg < 0.3:  # Mudança brusca pra baixo
    reset_current_song()
```

### 4. **Detecção por Sílêncio Prolongado** (3 segundos)
Unchanged from before - mantido como failover:
- Se espectro baixo por 60 frames (~3s): resetar música
- Garante detecção de pausa ou mudança silenciosa

## Mudanças no Código

### `src/worker_headless.py`

#### Novos campos no `__init__`:
```python
# Histórico de espectro
self._spectrum_history = []  # Últimas energias
self._max_spectrum_history = 20  # Manter 1s
self._spectrum_change_threshold = 0.4  # Limiar de mudança

# Rastreamento de confiança
self._last_confidence = None
self._low_confidence_count = 0
self._low_confidence_threshold = 0.5  # <50%
self._low_confidence_trigger = 2  # 2 em sequência
self._min_recognition_interval_s = 2.0  # Se baixa, reconhecer em 2s
```

#### Método novo: `_check_confidence_for_song_change()`
Extrai lógica de confiança para reduzir complexidade de `_handle_song_found()`:
- Incrementa contador se confiança baixa
- Retorna `True` se deve ignorar reconhecimento

#### `_check_silence_and_reset()` - expandido:
- Agora analisa **mudança de espectro** além de silêncio
- Mantém histórico de energias
- Detecta crossfades/mudanças bruscas

#### `_time_until_next_check()` - melhorado:
- Se confiança baixa: intervalo de 2s
- Se sem duração: 15s
- Se com duração: min(85% duração, 15s), máximo 15s

#### `_handle_song_not_found()` - aprimorado:
- Agora limpa histórico de confiança e espectro
- Log mais informativo

## Configurações em `config.ini`

Novas opções de ajuste em `[Recognition]`:

```ini
# Detecção por confiança
confidence_threshold = 0.5      # <50%
confidence_trigger = 2          # 2 em sequência
confidence_interval = 2.0       # segundos para re-reconhecer

# Detecção por espectro
spectrum_change_threshold = 0.4 # 40% mudança
spectrum_history_size = 20      # frames (~1s)
```

## Benefícios

| Cenário | Antes | Depois |
|---------|-------|--------|
| Mudança rápida sem silêncio | ~30s | ~2-5s (via confiança + espectro) |
| Crossfade | Pode não detectar | Detecta via espectro |
| Reconhecimento ambíguo | Continua rastreando | Reconhecer em 2s |
| Pausa/silêncio | ~3s | ~3s (unchanged) |
| Músicas muito curtas | >duração | ~5s mínimo |

## Ajustes Recomendados

Se ainda não detecta bem:
1. **Reduzir** `confidence_threshold` (ex: 0.4 em vez de 0.5)
2. **Aumentar** `spectrum_history_size` (ex: 30 frames = 1.5s)
3. **Reduzir** `spectrum_change_threshold` (ex: 0.3 em vez de 0.4)
4. **Reduzir** `tracking_interval` (ex: 20s em vez de 30s)

Se muito sensível (falsos positivos):
1. **Aumentar** `confidence_trigger` (ex: 3 em vez de 2)
2. **Aumentar** `spectrum_change_threshold` (ex: 0.5)
3. **Aumentar** `silence_frames_threshold` (ex: 80)

## Histórico Técnico

- **v0**: Silêncio + intervalo 30s
- **v1**: Adiciona detecção por confiança + espectro
- Intervalo reduzido para máximo 15s
- Rastreamento progressivo de baixa confiança

## Próximos Passos (FASE 2)

1. **Fingerprinting local** (chromaprint): hash robusto mesmo com áudio ruim
2. **Comparação com anterior**: se confidence baixa E hash diferente → mudança
3. **Cache de fingerprints**: evitar computação repetida
