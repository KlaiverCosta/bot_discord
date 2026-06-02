# Discord GPT Audio Bot

Bot para Discord desenvolvido em Python que integra inteligência artificial da OpenAI com reprodução de áudio em canais de voz.

O bot permite:

* Conversar com modelos GPT da OpenAI.
* Responder quando mencionado em canais de texto.
* Utilizar comando `/gpt` para perguntas diretas.
* Reproduzir áudio a partir de URLs (YouTube e outras fontes suportadas pelo yt-dlp).
* Gerenciar fila de reprodução.
* Criar uma biblioteca de áudios reutilizáveis por alias.
* Fazer upload de arquivos de áudio diretamente pelo Discord.
* Executar comandos de controle de reprodução (pause, resume, skip, stop).
* Armazenar contexto curto das conversas por canal.

---

## Funcionalidades

### Inteligência Artificial

* Resposta por menção ao bot.
* Comando `/gpt`.
* Memória curta por canal.
* Comando `/limpar_contexto`.

### Reprodução de Áudio

* Entrada automática em canais de voz.
* Reprodução por URL.
* Fila de reprodução.
* Download automático dos áudios.
* Limpeza automática de arquivos temporários.
* Saída automática da call quando a fila termina.

### Biblioteca de Áudios

* Upload de arquivos.
* Cadastro por URL.
* Reprodução por alias.
* Listagem dos áudios cadastrados.
* Remoção de áudios salvos.

---

## Tecnologias Utilizadas

* Python 3.10+
* Discord.py
* OpenAI API
* yt-dlp
* FFmpeg
* python-dotenv
* asyncio

---

## Estrutura do Projeto

```text
.
├── audio_library/
│   └── Áudios permanentes
│
├── downloads/
│   └── Arquivos temporários
│
├── logs/
│   └── bot.log
│
├── audio_db.json
├── .env
├── bot.py
└── requirements.txt
```

---

## Pré-requisitos

### Python

Instale Python 3.10 ou superior.

```bash
python --version
```

### FFmpeg

O bot utiliza FFmpeg para reproduzir áudio.

Ubuntu:

```bash
sudo apt update
sudo apt install ffmpeg
```

Verificação:

```bash
ffmpeg -version
```

---

## Instalação

Clone o projeto:

```bash
git clone https://github.com/KlaiverCosta/bot_discord.git
cd bot_discord
```

Crie ambiente virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

---

## Variáveis de Ambiente

Crie um arquivo `.env`:

```env
DISCORD_TOKEN=SEU_TOKEN_DISCORD
OPENAI_API_KEY=SUA_CHAVE_OPENAI

GPT_MODEL=gpt-5-mini
BOT_PREFIX=!
MAX_AUDIO_MB=25
MAX_AUDIO_MINUTES=20

# Opcional
GUILD_ID=123456789012345678
```

### Descrição

| Variável          | Descrição                          |
| ----------------- | ---------------------------------- |
| DISCORD_TOKEN     | Token do bot Discord               |
| OPENAI_API_KEY    | Chave da API OpenAI                |
| GPT_MODEL         | Modelo utilizado nas respostas     |
| BOT_PREFIX        | Prefixo para comandos tradicionais |
| MAX_AUDIO_MB      | Tamanho máximo permitido           |
| MAX_AUDIO_MINUTES | Duração máxima permitida           |
| GUILD_ID          | Servidor para sincronização rápida |

---

## Executando o Bot

```bash
python bot.py
```

Ao iniciar corretamente:

```text
Bot conectado como NomeDoBot
```

---

# Comandos Disponíveis

## IA

### /gpt

Faz uma pergunta para a IA.

```text
/gpt pergunta: Explique o que é Docker
```

### /limpar_contexto

Remove o histórico de conversa do canal atual.

---

## Voz

### /entrar

Faz o bot entrar no canal de voz.

### /sair

Sai do canal e limpa a fila.

### /status

Mostra informações do bot.

---

## Reprodução

### /tocar

Adiciona um áudio à fila.

```text
/tocar url:https://youtube.com/...
```

### /fila

Exibe a fila atual.

### /pause

Pausa o áudio atual.

### /resume

Retoma a reprodução.

### /skip

Pula o áudio atual.

### /stop

Para a reprodução e limpa toda a fila.

---

## Biblioteca de Áudios

### /adicionar

Adicionar por URL:

```text
/adicionar alias:risada url:https://...
```

Adicionar por upload:

```text
/adicionar alias:risada arquivo:[anexo]
```

### /falar

Reproduz um áudio salvo.

```text
/falar alias:risada
```

### /listar_audios

Lista todos os aliases cadastrados.

### /remover_audio

Remove um áudio da biblioteca.

```text
/remover_audio alias:risada
```

---

## Comandos Tradicionais

### !ping

Retorna:

```text
pong
```

---

## Segurança

O bot aplica validações para:

* URLs inválidas.
* Arquivos acima do limite configurado.
* Áudios com duração excessiva.
* Tipos de arquivo não suportados.
* Reprodução simultânea concorrente por servidor.

---

## Logs

Todos os eventos são registrados em:

```text
logs/bot.log
```

Incluindo:

* Erros.
* Reprodução de áudio.
* Conexões de voz.
* Operações da biblioteca.

---

## Licença

Este projeto está disponível para uso e modificação conforme a licença definida pelo proprietário do repositório.
