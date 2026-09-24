# Autollenado de Microsoft Forms desde Excel

Llena y envía el formulario <https://forms.office.com/r/bR7mhFvEKc> **una vez por cada fila** del Excel.
Es gratis, corre en tu propio navegador y no depende de ningún servidor, así que no vence.

## Formato del Excel

Primera hoja, primera fila con estos encabezados (el orden no importa):

| Código | Nombre del Cliente | Jefatura | Ruta | Descripción Canal | Territorio |
|---|---|---|---|---|---|

La pregunta 2 ("el cliente es para") siempre se responde **Negociar**.

La pregunta **Subcanal** se llena con la columna **Descripción Canal**:
- Si ese canal existe entre las opciones del formulario, se marca **exactamente ese**
  (sin buscar parecidos; `DROGUERIA` no se marca como `DROGUERÍA HM`).
  Si Excel trae el nombre cortado (p. ej. `INSTITUCIONES Y OFICINAS (ENTIDADES PRIV`),
  se marca la única opción que empieza igual.
- Si no existe, el subcanal **se deja en blanco** y el resto de la respuesta se envía normal.
  Al terminar, el panel lista qué filas se enviaron sin subcanal.

### Errores de escritura (en el Excel o en el formulario)

El script interpreta errores de escritura comparando letra por letra:
`COMIDAS RAPIDAS` (Excel) se reconoce como `OMIDAS RAPIDAS` (formulario) porque se parecen un 93 %.
Solo acepta la opción si se parece al menos un **85 %** (`SIMILITUD_MINIMA`) y si no hay otra opción
casi igual de parecida. Así, `DROGUERIA` (75 % parecido a `DROGUERÍA HM`) **no** se confunde.
Tampoco importan mayúsculas, tildes ni signos (`BAR / DISCOTECA` = `BAR/DISCOTECA`).

Para casos especiales, agrega a mano la equivalencia en `EQUIVALENCIAS` al inicio del script:

```js
const EQUIVALENCIAS = {
  'MINIMERCADOS': 'MINI MERCADO',   // "lo que dice el Excel": "lo que dice el formulario"
};
```

**Botón "Validar canales":** antes de enviar, compara todos los canales del Excel con las opciones
del formulario (sin llenar ni enviar nada) y muestra: cuáles son iguales, cuáles se **interpretaron**
(con el % de parecido, para que confirmes) y cuáles **no están** y quedarán en blanco.

## Instalación (una sola vez, ~3 minutos)

1. Instala la extensión gratuita **Tampermonkey** en Chrome o Edge: <https://www.tampermonkey.net/>.
2. En Chrome/Edge, abre `chrome://extensions` (o `edge://extensions`), activa **Modo de desarrollador**
   y, en los detalles de Tampermonkey, activa **Permitir scripts de usuario** si aparece.
3. Clic en el ícono de Tampermonkey → **Crear un nuevo script** → borra todo lo que aparece,
   pega el contenido de `autollenado-forms.user.js` y guarda (Ctrl+S).

## Uso (cada vez que tengas un Excel nuevo)

1. Abre el formulario. Abajo a la derecha aparece el panel **Autollenado desde Excel**.
2. Elige tu archivo `.xlsx`. El panel muestra cuántas filas encontró.
3. Pulsa **Validar canales** y revisa la lista de canales interpretados y los que quedarán en blanco.
   Luego pulsa **Probar (llenar sin enviar)**: llena el formulario con la primera fila **sin enviarlo**.
   Revisa que cada respuesta quedó en su pregunta (el panel lista qué puso en cada una).
4. Si todo está bien, recarga la página y pulsa **Enviar todas**. El script envía una fila,
   vuelve a abrir el formulario en blanco y sigue con la siguiente. Deja la pestaña abierta.

- **Pausar** detiene el proceso después del envío en curso; **Enviar todas** lo retoma.
- Si una fila falla (por ejemplo, una pregunta obligatoria que no se pudo llenar),
  el proceso se detiene en esa fila y te dice por qué. Puedes cambiar "Empezar en la fila #"
  para saltarla o continuar.
- El progreso se guarda: si cierras el navegador, al volver a abrir el formulario sigue en la fila pendiente.

## Si el formulario cambia

Al inicio del script está la lista `REGLAS`, que dice qué columna va en cada pregunta
(según una palabra del título de la pregunta). Si Microsoft cambia un título o agregas una columna,
solo ajusta esa lista.

**Nota:** si el formulario está configurado como "Una respuesta por persona", Microsoft
no permitirá enviar varias respuestas con la misma cuenta.
