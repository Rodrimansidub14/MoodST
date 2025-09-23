# MoodST — MCP-Powered Assistant for Music, Files & Git

---

## Table of Contents

1. [Project Overview (English)](#project-overview-english)
2. [Features](#features)
3. [Architecture Overview](#architecture-overview)
4. [Coding Standards & Documentation](#coding-standards--documentation)
5. [Requirements](#requirements)
6. [Installation](#installation)
7. [Configuration](#configuration-env)
8. [Running the App](#run-the-app)
9. [How to Use (in the app)](#how-to-use-in-the-app)
10. [Using MCP Externally (Inspector)](#using-mcp-externally-inspector)
11. [Troubleshooting](#troubleshooting)
12. [Project Structure](#project-structure)
13. [A note on the use of AI](#LLM-Use)
14. [Security Notes](#security-notes)

---

## Project Overview (English)

**MoodST** is a Streamlit-based assistant that leverages the Model Context Protocol (MCP) to connect with external tools and services, including Spotify (music), Filesystem (file management), and Git (version control). The assistant provides a conversational interface for music discovery, playlist management, file operations, and code versioning—all orchestrated through a modular, extensible architecture.

---
# MoodST — MCP-Powered Assistant for Music, Files & Git

MoodST is a Streamlit app that uses **Model Context Protocol (MCP)** to talk to external tools:
- **Spotify** (music search, recommendations, and playlist creation)
- **Filesystem** (local file read/write via MCP server)
- **Git** (init, add, commit via MCP server)

This README covers: project characteristics, implemented features, installation and usage on your machine, coding standards, and how to connect to MCP (both from the app and with external MCP clients).

---

## ✨ Features

- **Conversational assistant** (Spanish UI by default) with short “reasoning” peek (optional expander).
- **Spotify integration via MCP**:
  - Search tracks, get recommendations, explain selections.
  - Create **public or private playlists** and **return the shareable URL** in the bot’s response.
  - Build playlists from your profile and optionally start playback on a device.
- **Filesystem (MCP)**: create directories, write files as part of guided workflows (e.g., scaffolding a project).
- **Git (MCP)**: initialize a repository, add files, and commit—automatically ensures directory preconditions.
- **Robust OAuth flow**: in-app “Connect Spotify” button; auto-completes login when redirected with `?code=...`.
- **Graceful fallbacks**: if the planner LLM is overloaded or a tool fails, the app still provides helpful output.
- **Context handling**: remembers last “public/private” selection for playlists and accumulates found track IDs until you ask to create a playlist.
- **Telemetry/Logging hooks**: MCP plan & execution steps can be logged for debugging (`logger.log_mcp`).

---

## 🧱 Architecture Overview

- **UI**: `client/app.py` (Streamlit)
- **Planning/Finalization LLM**: `client/llm.py` (Gemini 2.5 series; configurable)
- **MCP Orchestration**: `client/mcp_client.py` (starts MCP servers on demand)
- **MCP Servers**:
  - **Spotify (Python)**: invoked via `MCP_SPOTIFY_ENTRY` (script path or module name).
  - **Filesystem (Node)**: `npx -y @modelcontextprotocol/server-filesystem <allowed_dirs>`.
  - **Git (Python)**: one server per repo; ensures `git init` with the local Git CLI.

> You do **not** need to start MCP servers manually for normal usage; the app starts them on demand.

---

## 🧭 Coding Standards & Documentation

MoodST follows Python best practices:
- **Type hints everywhere** (including return types).
- **Module/Class/Function docstrings** using **Google-style** (or NumPy style) with concise descriptions, argument/return sections, and error notes.
- **Inline comments** for non-obvious logic and platform-specific code paths (Windows/macOS/Linux).
- **Separation of concerns**: UI (Streamlit) vs. planning/finalization (LLM) vs. MCP orchestration.
- **Error handling**: user-friendly messages; structured logging of MCP plan/execution results.
- **Formatting & Linting** (recommended):
  - [Black](https://black.readthedocs.io/) for formatting
  - [Ruff](https://docs.astral.sh/ruff/) for linting (flake8/pyflakes rules, import sorting)
- **Docstrings quick checklist**:
  - Summary line (imperative mood), blank line, detailed description (if needed).
  - Args/Returns/Raises sections.
  - Example snippets where relevant.
- **Example commands**:

  ```bash
  # format
  black client mcp

  # lint
  ruff check client mcp
  ```

## 🧰 Requirements

- Python 3.11+ (3.12/3.13 recommended)

- Node.js 18+ (for the Filesystem MCP server via npx)

- Git (CLI installed and on PATH)

- A Spotify Developer account with an app created

- (Optional) An external MCP client for debugging (e.g., @modelcontextprotocol/inspector)

## 📦 Installation

```bash
# 1) Clone and enter the repo
git clone <your-repo-url>
cd MoodST

# 2) Create and activate a virtual environment
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 3) Install Python dependencies
pip install -r requirements.txt

# 4) (Optional) Install MCP Inspector for testing
npm i -g @modelcontextprotocol/inspector
```

## 🔐 Configuration (.env)

Create a `.env` file at the project root:
```
# === Gemini (planner/finalizer LLM) ===
GEMINI_API_KEY=your_api_key
GEMINI_MODEL=gemini-2.5-flash

# === Spotify OAuth ===
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret

# IMPORTANT: Use your Streamlit app URL as Redirect URI
# (must EXACTLY match the one configured in Spotify Dashboard)
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8501

# Scopes for playlist creation and playback control
SPOTIFY_SCOPES=playlist-read-private playlist-modify-private playlist-modify-public user-read-playback-state user-modify-playback-state

# === Optional: force the Spotify MCP server entrypoint ===
# If your Spotify MCP server is a local script:
MCP_SPOTIFY_ENTRY=C:\Users\you\path\to\MoodST\mcp\spotify\server.py
# Or, if it's an installable module:
# MCP_SPOTIFY_ENTRY=mcp_server_spotify
```
Spotify Developer Dashboard: add http://127.0.0.1:8501 (or http://localhost:8501) to your Redirect URIs. This must match SPOTIFY_REDIRECT_URI.


## ▶️ Run the App
```bash
streamlit run client/app.py
```

**First-time Spotify connect:**

1. Click “Conectar Spotify” in the UI.
2. Approve permissions on the Spotify page.
3. You’ll be redirected to `http://127.0.0.1:8501/?code=...`; the app automatically completes the login.
4. If the app later shows a connection link after a retry, just click “Conectar Spotify” again (the authorize_url is stored in session).

## 🎙️ How to Use (in the app)
- Ask for music: “Recomiéndame 5 canciones de rock and roll”.

- Make a playlist: “Crea una playlist pública con las mejores 10 de rock and roll”.

- The assistant accumulates track IDs found during the session; when you ask to create a playlist:

    - It calls the Spotify MCP server to create it.

    - Returns the playlist link in the assistant’s message, e.g.
        ✅ Created your playlist and added 10 tracks: https://open.spotify.com/playlist/...

## 🧪 Using MCP Externally (Inspector)

If you want to debug the Spotify MCP server without the UI:

Ensure your .env variables are loaded in your shell.

If your entrypoint is a local script (.py):

```powershell 
# Windows (PowerShell)
$env:PYTHONIOENCODING="utf-8"
mcp-inspector --command "python" --args "C:\Users\you\path\to\MoodST\mcp\spotify\server.py"
```

```bash
mcp-inspector --command "python" --args "-m" --args "mcp_server_spotify"
```
In Inspector:

- whoami → returns { authed: true/false, id, display_name }.

- If not authenticated:

    - auth_begin → returns authorize_url (open it, approve, then copy the full redirect URL containing ?code=...).

    - auth_complete with { "code": "<paste_code_here>" } or { "redirect_url": "<paste_full_url_here>" }.

- search_track example:

```json
{ "tool": "search_track", "args": { "query": "Queen Bohemian Rhapsody", "limit": 1 } }
```
- Create a playlist with tracks:
```json
{
  "tool": "create_playlist_with_tracks",
  "args": {
    "name": "My Mix • Rock & Roll",
    "track_ids": ["7tFiyTwD0nx5a1eklYtX2J", "5CQ30WqJwcep0pYcV4AMNc"],
    "public": true,
    "description": "Auto-generated from MCP tests"
  }
}
```
- Typical success response:
```json
{ "playlist_id": "…", "url": "https://open.spotify.com/playlist/…", "added": 2 }
```
## 🛠️ Troubleshooting

- Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET
Check your .env and ensure your shell session loaded those variables.

- invalid_client / invalid_grant / redirect_uri_mismatch
The Redirect URI in Spotify must match SPOTIFY_REDIRECT_URI exactly.

- Unknown tool: create_playlist
Make sure your Spotify MCP server version implements create_playlist.
As an alternative, use create_playlist_with_tracks (adds tracks in one step).

- “Conectar Spotify” button does not appear
The app stores authorize_url in st.session_state["last_auth_url"]. Try connecting again and check logs to confirm auth_begin was called.


## 📁 Project Structure 

```graphql
client/
  app.py             # Streamlit UI (CRT theme), MCP integration, OAuth UX
  llm.py             # Planner & finalizer (Gemini) with robust fallbacks
  mcp_client.py      # Starts and calls MCP servers on demand
  logger.py          # MCP logging utilities (used by app)
mcp/
  spotify/
    server.py        # Spotify MCP server (tools: auth, search, playlist, playback)
publish.py           # Optional: helper for publishing repos or assets
requirements.txt     # Python dependencies
README.md            # This file
```
## Note on the use of LLMS
Large Language Models (LLMs) played a significant role throughout this project's lifecycle, from initial concept development to final code implementation. This section outlines how these AI tools were leveraged responsibly and ethically.

### Ideation and Planning
- **Concept Development**: Used LLMs to brainstorm project ideas, explore different approaches, and evaluate feasibility
- **Architecture Design**: Leveraged AI assistance to discuss system design patterns and best practices
- **Research Support**: Employed LLMs to summarize relevant documentation, frameworks, and technical concepts

### Code Generation and Development
- **Boilerplate Creation**: Generated initial project structure, configuration files, and common patterns
- **Function Implementation**: Used AI assistance for specific algorithms, utility functions, and complex logic
- **Code Review**: Employed LLMs to identify potential issues, suggest improvements, and ensure best practices
- **Documentation**: Generated inline comments, docstrings, and README sections with AI support

### Ethical Considerations and Best Practices
- **Human Oversight**: All AI-generated code was thoroughly reviewed, tested, and validated by human developers
- **Intellectual Property**: Ensured all generated code complies with licensing requirements and doesn't violate copyrights
- **Transparency**: Openly documenting LLM usage to maintain project transparency and reproducibility
- **Quality Assurance**: Implemented comprehensive testing to verify the correctness and security of AI-assisted code
- **Learning Integration**: Used LLM interactions as learning opportunities rather than blind code copying

### Limitations and Mitigations
- **Code Verification**: Never deployed AI-generated code without thorough testing and validation
- **Context Awareness**: Recognized that LLMs may lack full project context and required human judgment for integration
- **Security Review**: Conducted additional security assessments on AI-generated components
- **Performance Optimization**: Manually optimized AI-generated code for project-specific requirements


## 🔐 Security Notes
- Keep your .env out of version control (use .gitignore).

- Restrict Spotify scopes to only what you need.

- Consider creating a separate Spotify app for local testing.



