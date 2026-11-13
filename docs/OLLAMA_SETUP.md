# Local AI Setup Guide (Ollama & DeepSeek)

Run **Capri AI Desktop** 100% offline on your Windows PC without cloud API keys using local LLMs.

## 1. Install Ollama
1. Download and install Ollama for Windows from [ollama.com](https://ollama.com).
2. Open PowerShell or Command Prompt and pull a recommended model:
   ```bash
   ollama pull deepseek-r1:8b
   # or for faster tool calling & screen automation:
   ollama pull qwen2.5:7b
   ```

## 2. Enable CORS for Desktop GUI
If Capri cannot connect to your local Ollama server, set the `OLLAMA_ORIGINS` environment variable:
1. Open Windows **System Properties → Environment Variables**.
2. Add a new User Variable:
   - **Variable name:** `OLLAMA_ORIGINS`
   - **Variable value:** `*`
3. Restart Ollama from the system tray.

## 3. Configure Capri
Update your `config.yaml` to route requests to your local instance:
```yaml
engine: custom
custom_endpoint: http://localhost:11434/v1
model: deepseek-r1:8b
permissions:
  mode: ask
```
