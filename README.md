# 🧠 WhatsApp AI Assistant (Flask + Groq + Twilio + SQLite)

This project is an intelligent, context-aware **WhatsApp chatbot** built with **Flask**, the **Twilio API**, **Groq (LLaMA-4-Scout)**, and **SQLite** for persistent conversation memory.

It's designed to maintain continuous chat flow, process user intents, and deliver contextual, human-like responses with the speed of Groq's LLM inference engine.

---

## 🚀 Features

* 🗨️ **WhatsApp Integration:** Seamless messaging using Twilio’s WhatsApp API.
* 🧠 **Context-Aware Memory:** Stores and retrieves previous *N* messages from SQLite for continuous, non-fragmented chat flow.
* ⚙️ **LLM-Powered Responses:** Utilizes the **Groq API** with the high-performance **LLaMA-4-Scout** model for rapid reasoning and natural dialogue generation.
* 💾 **SQLite Database:** Lightweight and persistent storage of all user interactions, ensuring conversations are never lost.
* 🔄 **Dynamic Prompting:** Adds system-level context and conversation history to every LLM request for highly relevant replies.

---

## 🧰 Tech Stack

| Component | Technology | Description |
| :--- | :--- | :--- |
| **Frontend** | WhatsApp via Twilio | User interface for interaction. |
| **Backend** | Flask (Python) | Lightweight web server handling webhooks. |
| **Database** | SQLite | Local, file-based storage for conversation memory. |
| **LLM Engine** | Groq API (`LLaMA-4-Scout`) | High-speed LLM inference provider. |
| **Messaging** | Twilio API | Gateway for sending and receiving WhatsApp messages. |

---

## ⚙️ How It Works

The assistant operates on a webhook-driven architecture to manage the full conversation lifecycle:

1.  **Twilio → Flask Webhook:** Twilio receives a user message and forwards it as an HTTP POST request to the `/whatsapp` endpoint in the Flask application.
2.  **Message Extraction:** Flask extracts essential details: the sender’s **phone number** and the **message content** (text or media).
3.  **Context Retrieval:** The system queries `conversation_history.db` to retrieve the last *N* messages (e.g., `limit=20`) associated with the sender's phone number.
4.  **Prompt Construction:** A structured `messages` list is built for the Groq API call.
5.  **LLM Response via Groq:** The prompt is sent to the Groq API for a rapid response.
6.  **Store & Reply:** The generated assistant response is saved to **SQLite** and then sent back to the user via the **Twilio API**.

### 🧠 Architecture Diagram

```mermaid
flowchart TD
    A[WhatsApp] --> B[Flask Webhook]
    B -->|Extract sender, message, media| C[(SQLite)]
    C -->|Get last N messages| D[Build messages list]
    D -->|Add persona + context| E[Groq API (LLaMA-4-Scout)]
    E -->|Generate contextual reply| F[(SQLite)]
    F -->|Save assistant reply| G[Twilio Response]
    G -->|Send reply to user| A
