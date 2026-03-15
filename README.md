# 🤖 AI Browser Agent

Autonomous AI agent that controls a native web browser to perform multi-step tasks using vision and intelligent planning.

## ✨ Features

- **Autonomous Browsing**: Uses `VisionAgent` to observe the page and decide on the next actions.
- **Persistent Sessions**: Keeps user data and logins between runs in `~/.agent_user_data`.
- **OpenRouter Integration**: Supports flexible LLM selection (GPT-4o, Claude 3.5 Sonnet, etc.).
- **Interactive CLI**: Task-based interface with rich terminal formatting.
- **DOM Compression**: Smart handling of large pages to stay within token limits.

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- [Node.js](https://nodejs.org/) (required for Playwright)

### Installation
1. **Clone the repository** (if applicable) or enter the project directory.
2. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Install Playwright browsers**:
   ```bash
   playwright install
   ```

### Configuration
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and add your `OPENROUTER_API_KEY`:
   ```env
   OPENROUTER_API_KEY=your_key_here
   LLM_MODEL=openai/gpt-4o  # Optional: change the model
   ```

## 🛠️ Usage

Run the agent via the main entry point:
```bash
python main.py
```
Once launched, enter your task in plain English (e.g., *"Find the cheapest flight from NYC to London for next Friday"*).

## 📂 Project Structure
- `main.py`: Interactive CLI entry point.
- `browser_controller.py`: Core logic for browser manipulation (Playwright).
- `agents/`: Contains agent implementation (`VisionAgent`, `BaseAgent`).
- `config.py`: Centralized configuration management.
- `js/`: Helper scripts for DOM extraction and element interaction.
- `prompts/`: System prompts for the AI agent.
