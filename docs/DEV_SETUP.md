# Floating Lyrics - Configuração de Desenvolvimento

Este guia explica como configurar ferramentas de desenvolvimento para detectar erros **antes** de executar o código.

## 🛠️ Ferramentas de Validação

### 1. Validação de Configuração (Recomendado)

Execute antes de iniciar o servidor para detectar problemas comuns:

```bash
# No diretório llm-music-api
python validate_config.py
```

Valida:
- ✅ Arquivo `.env` existe
- ✅ Dependências Node.js instaladas
- ✅ Dependências Python instaladas
- ✅ Caminho do modelo válido
- ✅ Porta disponível

### 2. Linting e Formatação com Ruff (Recomendado)

[Ruff](https://github.com/astral-sh/ruff) é um linter/formatter Python extremamente rápido que detecta:
- Erros de sintaxe
- Imports não usados
- Variáveis não definidas
- Anti-patterns comuns
- Problemas de estilo

**Instalação:**

```bash
pip install ruff
```

**Uso:**

```bash
# Windows PowerShell/CMD
lint.bat              # Verificar código
lint.bat --fix        # Corrigir automaticamente
lint.bat format       # Formatar código

# Git Bash/Linux
./lint.sh             # Verificar código
./lint.sh --fix       # Corrigir automaticamente
./lint.sh format      # Formatar código

# Alternativa (qualquer terminal com venv ativo)
python -m ruff check .
python -m ruff check --fix .
python -m ruff format .
```

**Integração com VS Code:**

Adicione ao `.vscode/settings.json`:

```json
{
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": "explicit",
      "source.organizeImports.ruff": "explicit"
    }
  }
}
```

Instale a extensão: [Ruff for VS Code](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)

### 3. Type Checking com MyPy (Opcional)

MyPy detecta erros de tipo em tempo de desenvolvimento:

```bash
# Instalação
pip install mypy

# Executar type checking
mypy src/ llm-music-api/src/
```

### 4. Pre-commit Hooks (Opcional, mas Altamente Recomendado)

Executa validações **automaticamente** antes de cada commit:

**Instalação:**

```bash
pip install pre-commit
pre-commit install
```

**Uso:**

Agora, a cada `git commit`, os hooks executarão automaticamente:
- Ruff linting
- Ruff formatting
- Verificação de YAML/JSON
- Detecção de chaves privadas
- Remoção de trailing whitespace

Para executar manualmente:

```bash
pre-commit run --all-files
```

## 🎯 Fluxo de Trabalho Recomendado

### Setup Inicial (uma vez)

```bash
# 1. Ativar ambiente virtual
# Windows PowerShell/CMD:
.venv\Scripts\activate.bat
# Git Bash/Linux:
source .venv/Scripts/activate

# 2. Instalar ferramentas de dev
pip install -r requirements_dev.txt

# 3. Instalar pre-commit hooks (opcional)
pre-commit install

# 4. Configurar VS Code (se usar)
# - Copiar .vscode/settings.json.example para .vscode/settings.json
# - Instalar extensão Ruff (charliermarsh.ruff)
```

### Antes de Iniciar o Servidor

```bash
# Validar configuração
cd llm-musice corrigir código antes de commitar
./lint.sh --fix      # Git Bash/Linux
lint.bat --fix       # Windows

### Durante o Desenvolvimento

```bash
# Verificar código antes de commitar
ruff check --fix .
ruff format .

# Ou deixe o pre-commit fazer automaticamente no commit
git add .
git commit -m "feat: nova feature"  # hooks executam automaticamente
```

### Integração Contínua (CI)

Adicione ao GitHub Actions ou CI de sua escolha:

```yaml
- name: Lint com Ruff
  run: |
    pip install ruff
    ruff check .
```

## 📋 Checklist de Desenvolvimento

Antes de fazer push:

- [ ] `./lint.sh --fix` (ou `lint.bat --fix` no Windows) sem erros
- [ ] `cd llm-music-api && python validate_config.py` passou
- [ ] Testes manuais funcionando
- [ ] (Opcional) `python -m mypy src/` sem erros críticos

## 🔧 Troubleshooting

### "Muitos erros de linting"

Se o Ruff reportar muitos erros em código legado:

1. Corrija gradualmente: `./lint.sh --fix` ou `lint.bat --fix`
2. Ignore regras específicas editando `pyproject.toml`
3. Use `# noqa: <código>` em linhas específicas

### "ruff not found no PowerShell"

O ruff está instalado no ambiente virtual Python. Use:
- Scripts auxiliares: `lint.bat` (Windows) ou `./lint.sh` (Git Bash)
- Ou ative o venv primeiro: `.venv\Scripts\activate.bat` e depois `python -m ruff check .`

### "Pre-commit muito lento"

Pre-commit pode ser pulado temporariamente:

```bash
git commit --no-verify -m "mensagem"
```

**Mas evite fazer isso regularmente!**

## 📚 Recursos

- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [MyPy Documentation](https://mypy.readthedocs.io/)
- [Pre-commit Documentation](https://pre-commit.com/)
