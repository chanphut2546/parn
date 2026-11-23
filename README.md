# 🌱 LINE-Controlled Smart Watering System (IoT)

ระบบรดน้ำอัตโนมัติควบคุมผ่าน LINE Chatbot — โครงงานปริญญานิพนธ์ สาขาเทคโนโลยีสารสนเทศ มหาวิทยาลัยศิลปากร (2569)

An IoT system that lets users turn watering on/off and set watering schedules through a LINE Chatbot. It is designed to be simple enough for elderly users.

---

## ✨ Features

- 💬 **LINE Chatbot control**: turn the water pump on/off and set watering schedules from LINE
- ⏰ **Scheduled watering**: water plants automatically at the set times
- 📊 **Dashboard**: view temperature history on a web dashboard
- 🔔 **LINE notifications**: get alerts about the system status
- 🛡️ **Failsafe**: the pump stops automatically when a device loses its connection

## 🛠️ Tech Stack

| Part | Technology |
|---|---|
| Microcontroller | ESP32 (C++ / Arduino) |
| Server | Raspberry Pi 4 |
| Backend | Python (Flask), REST API |
| Database | SQLite |
| Chatbot | LINE Messaging API |
| Version Control | Git / GitHub |

## 🏗️ System Overview

```
LINE App  ⇄  LINE Messaging API  ⇄  Flask Server (Raspberry Pi 4)  ⇄  ESP32  →  Water Pump / Sensors
                                          │
                                       SQLite  →  Dashboard
```

## 📐 Design Documents

- Requirement analysis
- Flowchart
- Use Case Diagram
- State Diagram

## 👥 Team

Team project (2 members). My work covered requirement analysis, system design, development, and testing.

## 🧪 Testing

- Tested LINE Chatbot commands for both normal use and invalid user input
- Tested the failsafe by simulating device disconnection to make sure the pump stops and an alert is sent

---

**Kanthicha Chanphut** · chanphut46@gmail.com
