# Autollenado de Microsoft Forms desde Excel

Llena y envía el formulario <https://forms.office.com/r/bR7mhFvEKc> **una vez por cada fila** del Excel.
Es gratis, corre en tu propio navegador y no depende de ningún servidor, así que no vence.

## Formato del Excel

Primera hoja, primera fila con estos encabezados (el orden no importa):

| Código | Nombre del Cliente | Jefatura | Ruta | Descripción Canal | Territorio |
|---|---|---|---|---|---|

La pregunta 2 ("el cliente es para") siempre se responde **Negociar**.

## Instalación (una sola vez, ~3 minutos)

1. Instala la extensión gratuita **Tampermonkey** en Chrome o Edge: <https://www.tampermonkey.net/>.
2. En Chrome/Edge, abre `chrome://extensions` (o `edge://extensions`), activa **Modo de desarrollador**
   y, en los detalles de Tampermonkey, activa **Permitir scripts de usuario** si aparece.
3. Clic en el ícono de Tampermonkey → **Crear un nuevo script** → borra todo lo que aparece,
   pega el contenido de `autollenado-forms.user.js` y guarda (Ctrl+S).

## Uso (cada vez que tengas un Excel nuevo)

1. Abre el formulario. Abajo a la derecha aparece el panel **Autollenado desde Excel**.
2. Elige tu archivo `.xlsx`. El panel muestra cuántas filas encontró.
3. Pulsa **Probar (llenar sin enviar)**: llena el formulario con la primera fila **sin enviarlo**.
   Revisa que cada respuesta quedó en su pregunta (el panel lista qué puso en cada una).
4. Si todo está bien, recarga la página y pulsa **Enviar todas**. El script envía una fila,
   vuelve a abrir el formulario en blanco y sigue con la siguiente. Deja la pestaña abierta.

- **Pausar** detiene el proceso después del envío en curso; **Enviar todas** lo retoma.
- Si una fila falla (por ejemplo, un canal que no existe en las opciones del formulario),
  el proceso se detiene en esa fila y te dice por qué. Puedes cambiar "Empezar en la fila #"
  para saltarla o continuar.
- El progreso se guarda: si cierras el navegador, al volver a abrir el formulario sigue en la fila pendiente.

## Si el formulario cambia

Al inicio del script está la lista `REGLAS`, que dice qué columna va en cada pregunta
(según una palabra del título de la pregunta). Si Microsoft cambia un título o agregas una columna,
solo ajusta esa lista.

**Nota:** si el formulario está configurado como "Una respuesta por persona", Microsoft
no permitirá enviar varias respuestas con la misma cuenta.
