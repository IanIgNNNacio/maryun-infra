# Ticket para OVH — dos reinicios en frío sin causa en el sistema operativo

**Estado: redactado, sin enviar.** Abrir un ticket es mandar un mensaje en tu
nombre a un tercero, así que lo dejo listo para que lo pegues tú.

Servidor: **maryun01**, `148.113.168.13`.

---

## Texto para pegar en el ticket

> **Asunto:** Dos reinicios en frío no solicitados en 35 horas, sin rastro en el
> sistema operativo — maryun01 / 148.113.168.13
>
> Buenas tardes,
>
> El servidor se reinició por sí solo dos veces en 35 horas. En ninguno de los
> dos casos hay rastro de apagado en el sistema operativo: la bitácora de
> `systemd` termina de golpe, sin la secuencia de apagado ordenado
> (`systemd-shutdown`, «Reached target Shutdown», sincronización de sistemas de
> archivos). El servidor volvió solo las dos veces.
>
> Fechas y duración, tomadas de `journalctl --list-boots` (UTC):
>
> | Último registro antes del corte | Primer registro al volver | Sin servicio |
> |---|---|---|
> | 2026-09-01 03:33:52 | 2026-09-01 03:35:51 | 119 s |
> | 2026-09-02 14:47:22 | 2026-09-02 14:49:12 | 109 s |
>
> Lo que hacía la máquina en el momento del segundo corte era trabajo normal:
> la última línea es un `docker container inspect` de rutina del agente de
> monitoreo. Sin picos de carga, sin errores previos, sin mensajes del núcleo
> en los minutos anteriores.
>
> Descartado desde el sistema operativo:
>
> - **No fue falta de memoria.** No hay ninguna muerte por OOM en la bitácora, y
>   el uso habitual es de 11 GB de 62.
> - **No fue temperatura ni disco.** No hay avisos térmicos ni errores de E/S
>   antes de ninguno de los dos cortes.
> - **No fue un apagado pedido.** No hay `shutdown`, `reboot` ni
>   `systemd-shutdown` en las últimas líneas de ninguno de los dos arranques
>   afectados. Sí aparecen en un tercer reinicio, el del 2026-09-01 03:59:29,
>   que fue nuestro y ordenado: sirve de contraste de cómo se ve uno normal.
>
> El único dato de hardware que veo es un Machine Check Exception que aparece
> **en cada arranque**, con la misma palabra de estado y en núcleos distintos:
>
> ```
> mce: [Hardware Error]: CPU 7: Machine Check: 0 Bank 6: baa0000000000118
> mce: [Hardware Error]: TSC 0 MISC d01a000000000000 SYND 4d000000 IPID 600b000000000
> mce: [Hardware Error]: PROCESSOR 2:a20f12 TIME 1788360538 SOCKET 0 APIC 12 microcode a201213
> ```
>
> Los contadores de EDAC están en cero, tanto corregidos como no corregidos, así
> que no parece memoria. Entiendo que un MCE de Bank 6 al arrancar es habitual y
> benigno en esta plataforma; lo incluyo por si en su registro de hardware
> significa algo junto con los dos reinicios.
>
> Lo que les pido:
>
> 1. Que revisen el **registro de eventos del sistema (IPMI SEL)** y el registro
>    de la fuente de alimentación en esas dos marcas de tiempo. Es lo único que
>    yo no puedo ver desde dentro.
> 2. Si hay constancia de un corte de alimentación, un evento térmico o un
>    reinicio por vigilante (watchdog), saberlo.
> 3. Si no hay constancia de nada, saber también eso: significaría que el corte
>    no lo originó la infraestructura y habría que mirar la placa o la memoria.
>
> Datos de la máquina:
>
> - Placa: ASRock Rack B550D4U-2T
> - BIOS: L0.37H, del 30 de enero de 2026
> - CPU: AMD Ryzen 9 5900X, 12 núcleos
> - RAM: 62 GiB
> - Sistema: Ubuntu, núcleo actual en pie desde 2026-09-02 14:49:12 UTC
>
> Si hace falta que deje el servidor disponible para una prueba de memoria, se
> puede coordinar: hay servicios en producción, pero acepto una ventana avisando
> con antelación.
>
> Gracias,
> Ian

---

## Cómo se obtuvo cada dato, para poder repetirlo

```bash
sudo journalctl --list-boots
sudo journalctl -b -1 -n 40 -o short-iso        # cómo termina un corte abrupto
sudo journalctl -b -2 -n 40 -o short-iso        # cómo termina un apagado ordenado
sudo journalctl -b 0 | grep -iE "mce|Hardware Error"
cat /sys/devices/system/edac/mc/mc0/ce_count    # 0
cat /sys/devices/system/edac/mc/mc0/ue_count    # 0
```

## Corrección de lo que dije antes

En el resumen anterior hablé de **un** reinicio en frío, el del 2 de
septiembre. Al reunir la evidencia para este ticket aparecieron **dos**, y el
del 1 de septiembre a las 03:33:52 UTC tiene la misma forma: final de bitácora
de golpe y vuelta sola en dos minutos. Dos veces en 35 horas es un patrón, y
eso hace el ticket bastante más sólido que un incidente aislado.

El tercer reinicio de esa noche, el de 03:59:29, sí fue ordenado y no cuenta.
