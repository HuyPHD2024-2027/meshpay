# Etherlink MeshPay - Decentralized Offline Payment Network

A revolutionary DeFi protocol that enables seamless offline payments through a self-healing mesh network architecture. Built for the Etherlink hackathon, this project combines IEEE 802.11s mesh networking with a pre-funded side-chain to enable sub-second retail payments during Internet outages.

---

## 📋 Prerequisites

* Ubuntu 20.04 (LTS) or later with kernel ≥ 5.x
* Python 3.8+
* Root privileges (for network namespaces)
* Mininet-WiFi 2.4.0+ — install via:
  ```bash
  git clone https://github.com/HuyPHD2024-2027/smart-pay.git
  mv smart-pay mininet-wifi
  cd mininet-wifi
  sudo util/install.sh -Wln
  ```

---

## ⚡ Quick Demo

```bash
# Start a 3-authority mesh with CLI
sudo python3 -m mn_wifi.examples.meshpay_demo --authorities 3 --clients 0 --internet
```

Inside the CLI you can issue DeFi commands such as:
```bash
transfer user1 user2 100
broadcast_confirmation user1
balance user1
balance user2
infor auth1
update_onchain_balance auth1
```

---

## 📞 Contact

*Huy D. Q.* — [huydo21052000@gmail.com]

---

> © 2025 Huy D. Q. Licensed under Apache 2.0. Powered by Mininet-WiFi.