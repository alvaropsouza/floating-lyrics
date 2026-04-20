# Scripts de Desenvolvimento

Esta pasta contém scripts auxiliares para desenvolvimento e manutenção do projeto.

## Scripts Disponíveis

### Linting e Formatação
- **`lint.bat`** / **`lint.sh`** - Executa Ruff para verificar código Python
  ```bash
  ./lint.sh          # Verificar código
  ./lint.sh --fix    # Corrigir automaticamente
  ./lint.sh format   # Formatar código
  ```

### Desenvolvimento
- **`dev_auto_restart.py`** - Auto-restart do servidor ao detectar mudanças em arquivos
- **`test_recognition.py`** - Testa reconhecimento de música com arquivo de áudio

### Monitoramento
- **`watch_logs.bat`** - Monitora logs do servidor em tempo real (Windows)

### Gerenciamento de Processos
- **`manage_processes.bat`** - Gerencia processos Python em execução (Windows)

## Uso

Execute os scripts a partir da **raiz do projeto** ou de dentro da pasta `scripts/`.

Exemplo:
```bash
# Da raiz
./scripts/lint.sh

# De dentro da pasta scripts/
cd scripts
./lint.sh
```
