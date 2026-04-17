# Floating Lyrics

Exibe as letras de músicas tocando no seu PC em uma janela flutuante e transparente, sincronizadas em tempo real com o áudio do sistema.

```
┌─────────────────────────────────────────────────────┐
│ Daft Punk — Get Lucky                               │
├─────────────────────────────────────────────────────┤
│  We're up all night to get some                     │
│  WE'RE UP ALL NIGHT FOR GOOD FUN                    │  ← linha atual
│  We're up all night to get lucky                    │
└─────────────────────────────────────────────────────┘
```

---

## Funcionalidades

| Recurso | Detalhe |
|---|---|
| Captura de áudio | WASAPI loopback — saída do sistema, não microfone |
| Reconhecimento | API **AudD** (gratuita, sem cartão) |
| Letras sincronizadas | **lrclib.net** (LRC com timestamps) |
| Fallback de letras | **Musixmatch** (texto simples, chave opcional) |
| Sincronização LRC | Usa o campo `timecode` do AudD para alinhar a letra |
| Overlay | Frameless, semi-transparente, always-on-top, arrastável |
| Configuração | `config.ini` — persiste posição, tamanho, fontes, chaves |
| Empacotamento | PyInstaller → `.exe` standalone |

---

## Pré-requisitos

- **Windows 10/11** (WASAPI loopback não está disponível em outros sistemas)
- **Python 3.10 ou superior**
- Conexão com a internet (para AudD e lrclib)

---

## Instalação

```bash
# 1. Clone ou extraia o projeto
cd floating-lyrics

# 2. Crie e ative um ambiente virtual (recomendado)
python -m venv .venv
.venv\Scripts\activate

# 3. Instale as dependências
pip install -r requirements.txt
```

> **Nota sobre pyaudiowpatch:** é um fork do PyAudio específico para WASAPI
> loopback no Windows. Se a instalação falhar, tente antes:
> ```bash
> pip install pipwin
> pipwin install pyaudio
> pip install pyaudiowpatch
> ```

---

## Configuração de chaves de API

### AudD (obrigatório para reconhecimento)

1. Acesse <https://dashboard.audd.io/>
2. Clique em **"Sign Up"** → crie uma conta gratuita (sem cartão de crédito)
3. Copie sua **API Token** no painel
4. Cole no campo **"AudD API Key"** dentro do app, ou edite `config.ini`:

```ini
[API]
audd_api_key = sua_chave_aqui
```

> **Sem chave:** o AudD aceita ~10 requisições/dia por IP no modo de teste.
> O plano gratuito com chave oferece **100 reconhecimentos/mês**.

---

### Musixmatch (opcional — fallback de letras)

Usado automaticamente quando o lrclib.net não encontra a letra.

1. Acesse <https://developer.musixmatch.com/>
2. Clique em **"Get a Free API Key"** e crie uma conta
3. Cole a chave em `config.ini`:

```ini
[API]
musixmatch_api_key = sua_chave_aqui
```

> **lrclib.net** não requer chave de API e é sempre tentado primeiro.

---

## Executando

```bash
python main.py
```

Modo desenvolvimento (reinicio automatico ao salvar alteracoes):

```bash
python dev_auto_restart.py
```

A janela de controle abrirá junto com o overlay de letras.

1. Insira sua chave AudD → clique **"Salvar chaves"**
2. Toque uma música no PC
3. Clique **▶ Iniciar**
4. O app captura 10 s de áudio, identifica a música e exibe as letras sincronizadas

---

## Parâmetros configuráveis (`config.ini`)

| Seção | Chave | Padrão | Descrição |
|---|---|---|---|
| `Recognition` | `capture_duration` | `10` | Segundos capturados por ciclo |
| `Recognition` | `recognition_interval` | `30` | Pausa entre reconhecimentos (s) |
| `Recognition` | `silence_threshold` | `100` | RMS mínimo; abaixo disso o envio é ignorado |
| `Display` | `opacity` | `0.85` | Opacidade do overlay (0.1–1.0) |
| `Display` | `font_size` | `16` | Tamanho da fonte em pontos |
| `Display` | `font_color` | `#FFFFFF` | Cor do texto |
| `Display` | `bg_color` | `#1A1A2E` | Cor de fundo |
| `Display` | `always_on_top` | `true` | Overlay sempre visível |
| `Display` | `lines_context` | `2` | Linhas de contexto acima/abaixo |

---

## Como usar o overlay durante jogos

- O overlay usa `Qt.WindowStaysOnTopHint` — funciona com jogos em **modo janela** e **borderless fullscreen**.
- Para jogos em **fullscreen exclusivo** (DirectX fullscreen), o overlay pode não aparecer sobre o jogo. Use o modo **borderless** no jogo para garantir visibilidade.
- **Arrastar:** clique e segure em qualquer área do overlay.
- **Redimensionar:** arraste o canto inferior direito (triângulo cinza).
- **Menu rápido:** clique com o botão direito no overlay.

---

## Ignorar áudio do Discord (roteamento por dispositivo)

O Windows loopback captura o áudio do dispositivo de saída inteiro. Para excluir
o Discord, configure o Discord para tocar em outro dispositivo e faça o app
capturar apenas o dispositivo principal da música.

No `config.ini`, seção `Audio`:

```ini
[Audio]
capture_device_index = -1
capture_device_name = Speakers
```

Regras:
- `capture_device_index >= 0`: usa esse índice exato (prioridade maior)
- `capture_device_name`: busca por nome parcial (ex.: `Speakers`, `Headphones`)
- se ambos vazios/default, usa o dispositivo padrão do Windows

Depois de alterar, reinicie o app.

---

## Empacotamento em `.exe` standalone (PyInstaller)

```bash
pip install pyinstaller

pyinstaller ^
  --onefile ^
  --windowed ^
  --name "FloatingLyrics" ^
  --add-data "config.ini;." ^
  main.py
```

O executável será gerado em `dist\FloatingLyrics.exe`.

> **Dica:** copie o `config.ini` para a mesma pasta do `.exe` antes de
> distribuir, para que o usuário possa editar as chaves de API sem precisar
> abrir o código.

---

## Estrutura do projeto

```
floating-lyrics/
├── main.py                  # Ponto de entrada
├── config.py                # Gerenciador de configuração
├── config.ini               # Preferências do usuário
├── requirements.txt
├── README.md
└── src/
    ├── audio_capture.py     # Captura WASAPI loopback via pyaudiowpatch
    ├── song_recognition.py  # Integração com a API AudD
    ├── lyrics_fetcher.py    # lrclib.net + Musixmatch (fallback)
    ├── lyrics_parser.py     # Parser de formato LRC
    ├── worker.py            # QThread: pipeline captura → reconhece → letra
    └── ui/
        ├── main_window.py   # Janela de controle (PyQt6)
        └── lyrics_window.py # Overlay flutuante (PyQt6, frameless)
```

---

## Sincronização de letras — como funciona

```
Tempo t₀ = início da captura (time.time())
           │
           │  ←── 10s de áudio capturado ──►
           │
           AudD responde: timecode = "1:23" (83 000 ms)
           │  → o áudio capturado veio do segundo 83s da música
           │
Posição atual = timecode_ms + (time.time() − t₀) × 1000
```

O campo `timecode` do AudD aponta **onde na música** o trecho capturado se encaixa, permitindo sincronização LRC precisa sem integração com o player.

---

## Solução de problemas

| Sintoma | Solução |
|---|---|
| `pyaudiowpatch não está instalado` | `pip install pyaudiowpatch` |
| `Nenhum dispositivo de loopback encontrado` | Verifique se há um dispositivo de áudio ativo (alto-falantes ou fones ligados) |
| `Nenhum áudio detectado (RMS < 100)` | Aumente o volume ou reduza `silence_threshold` em `config.ini` |
| `Música não reconhecida` | Aumente `capture_duration`; verifique sua cota AudD |
| `Limite da API AudD atingido` | Aguarde a renovação ou obtenha uma chave gratuita |
| Overlay não aparece sobre o jogo | Use modo borderless/janela no jogo |

---

## Licença

MIT — use, modifique e distribua livremente.
