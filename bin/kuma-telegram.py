"""Agrega el canal de Telegram a Uptime Kuma y lo engancha a todos los monitores."""
import os
import time

from uptime_kuma_api import UptimeKumaApi, NotificationType


def ids_de(monitor):
    """notificationIDList llega como lista o como dict segun la version."""
    ids = monitor.get("notificationIDList") or []
    if isinstance(ids, dict):
        return [int(k) for k, v in ids.items() if v]
    return list(ids)


a = UptimeKumaApi("http://uptime-kuma:3001", timeout=60)
a.login(os.environ["KUMA_USUARIO"], os.environ["KUMA_PASS"])

if any(n["name"] == "Telegram" for n in a.get_notifications()):
    print("  el canal de Telegram ya existia")
else:
    a.add_notification(
        name="Telegram",
        type=NotificationType.TELEGRAM,
        isDefault=True,
        applyExisting=True,
        telegramBotToken=os.environ["TELEGRAM_TOKEN"],
        telegramChatID=os.environ["TELEGRAM_CHAT"],
        telegramSendSilently=False,
    )
    print("  canal de Telegram creado")

# applyExisting SI funciona, pero tarda en propagarse: consultado enseguida
# devuelve listas vacias y parece que fallo. Por eso se espera antes de mirar.
time.sleep(6)

canales = {n["id"]: n["name"] for n in a.get_notifications()}
tid = next(i for i, n in canales.items() if n == "Telegram")

for m in a.get_monitors():
    ids = ids_de(m)
    if tid in ids:
        continue
    try:
        a.edit_monitor(m["id"], notificationIDList=sorted(set(ids) | {tid}))
    except Exception as e:
        print(f"  ! {m['name']}: {str(e)[:70]}")

time.sleep(4)
print()
print("=== verificacion ===")
sin = []
for m in sorted(a.get_monitors(), key=lambda x: x["name"]):
    ids = ids_de(m)
    nombres = ", ".join(canales.get(i, str(i)) for i in ids) or "sin canal"
    if tid not in ids:
        sin.append(m["name"])
    print(f"  {m['name']:20} {nombres}")

total = len(a.get_monitors())
print()
print(f"  {total - len(sin)} de {total} avisan por Telegram")
if sin:
    print("  SIN Telegram: " + ", ".join(sin))
a.disconnect()
