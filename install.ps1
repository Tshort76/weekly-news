# One line to a working weekly digest, on Windows.
#
#   irm https://raw.githubusercontent.com/Tshort76/weekly-news/main/install.ps1 | iex
#
# Installs uv if it is missing, installs the tool, and opens the app.
$ErrorActionPreference = "Stop"
$extras = if ($env:DIGEST_EXTRAS) { $env:DIGEST_EXTRAS } else { "ui" }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv (the Python installer this uses)..."
  irm https://astral.sh/uv/install.ps1 | iex
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (Get-Command ollama -ErrorAction SilentlyContinue) {
  Write-Host "Found Ollama - setting up to run models on this machine."
  $extras = "$extras,ollama"
} else {
  Write-Host "No Ollama found. Setup will offer a hosted model, or you can"
  Write-Host "install Ollama from ollama.com first and run this again."
}

uv tool install "weekly-news[$extras]"

Write-Host ""
Write-Host "Installed. Opening the app..."
digest open
