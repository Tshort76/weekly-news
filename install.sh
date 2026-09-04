#!/bin/sh
# One line to a working weekly digest, on macOS or Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/Tshort76/weekly-news/main/install.sh | sh
#
# Installs uv if it is missing, installs the tool, and opens the app. Honest
# about who this serves: if you have already installed Ollama you have used a
# terminal, and this is one more line. If you have never opened one, this is
# still a terminal, and that is the step we have not removed yet.
set -eu

EXTRAS="${DIGEST_EXTRAS:-ui}"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (the Python installer this uses)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv puts itself here and the current shell has not been told.
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

if command -v ollama >/dev/null 2>&1 || [ -d /Applications/Ollama.app ]; then
  echo "Found Ollama — setting up to run models on this machine."
  EXTRAS="$EXTRAS,ollama"
else
  echo "No Ollama found. Setup will offer a hosted model, or you can install"
  echo "Ollama from ollama.com first and run this again."
fi

uv tool install "weekly-news[$EXTRAS]"

echo
echo "Installed. Opening the app..."
exec "$HOME/.local/bin/digest" open
