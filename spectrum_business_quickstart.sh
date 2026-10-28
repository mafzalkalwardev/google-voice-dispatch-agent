#!/bin/bash
# Spectrum Business Sales Agent Quick Start
# Usage: ./spectrum_business_quickstart.sh [contacts_file] [agent_name]

set -e

CONTACTS_FILE="${1:-contacts.csv}"
AGENT_NAME="${2:-Jason}"
CALLBACK_NUMBER="${3:-+15551234567}"

echo "================================"
echo "Spectrum Business Agent Launcher"
echo "================================"
echo ""
echo "Configuration:"
echo "  Agent Name: $AGENT_NAME"
echo "  Callback Number: $CALLBACK_NUMBER"
echo "  Contacts File: $CONTACTS_FILE"
echo ""
echo "Starting Google Voice Dispatch Agent in Spectrum Business mode..."
echo ""

# Run with Spectrum Business agent type
python -m src.main \
  --agent-type spectrum \
  --contacts "$CONTACTS_FILE" \
  --agent-name "$AGENT_NAME" \
  --callback-number "$CALLBACK_NUMBER" \
  --realtime \
  --limit 10

echo ""
echo "Spectrum Business campaign complete!"
