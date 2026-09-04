# BC250 LLM MODE quick start

This is the shortest supported path from a prepared BC-250 to a local chat.
Use the full [end-user guide](end-user-guide.md) for installation, backups,
advanced host mode, and recovery details.

## Before opening the app

- Use a supported Bazzite or CachyOS BC-250 with adequate cooling.
- Configure approximately 12 GiB GPU UMA and 4 GiB host memory in firmware.
- Keep at least 20 GiB free; a large model needs more download/staging space.
- Start from the normal graphical desktop, not a plain SSH shell.

## Reach the first response

1. Open **BC250 LLM MODE**. The five setup chapters show an expected duration,
   what may change, and whether progress can resume.
2. Complete the read-only machine check and mandatory Safety acknowledgment.
3. Let **Prepare system** finish. Administrator approval is requested only for
   the reviewed host changes shown in the preview.
4. In **Choose workload**, start with **Interactive**. Pick a recommended model
   whose fit says **Fits comfortably**.
5. Select **Install, Start and Chat**. Activity owns pause, safe stop, resume,
   and recovery while the download and verification run.
6. When verification finishes, choose **Open Chat** and send one of the example
   prompts. The model name and workload remain visible above the conversation.

Drafts and conversations are stored locally. Restarting the machine returns to
the graphical desktop and does not automatically start a model.

## Connect another app

Open **Connections → Connect a device** and choose the client type. Save the
one-time key immediately. Copy the exact Base URL and model name; the Base URL
ends once in `/v1`. Run the selected client's test before leaving the page.

Use the Open WebUI browser address only in a browser. Phone/desktop model apps
use the separate OpenAI Base URL, not an Open WebUI `/api` address. Public
Tailscale Funnel remains off.

If anything fails, use **Connections → Connection Doctor** or follow the
[troubleshooting guide](troubleshooting.md).
