// ==UserScript==
// @name         Autollenado Microsoft Forms desde Excel (ML rutas)
// @namespace    https://github.com/felipebarv25
// @version      1.0.0
// @description  Carga un Excel (Código, Nombre del Cliente, Jefatura, Ruta, Descripción Canal, Territorio) y envía una respuesta del formulario por cada fila.
// @match        https://forms.office.com/*
// @match        https://forms.cloud.microsoft/*
// @match        https://forms.microsoft.com/*
// @require      https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  /* =====================================================================
   *  CONFIGURACIÓN — lo único que podrías necesitar editar
   * ===================================================================== */

  // Cómo se relaciona cada pregunta del formulario con el Excel.
  // Se revisan en orden; la PRIMERA regla cuyo patrón aparezca en el título
  // de la pregunta es la que se usa.
  //   pregunta: texto (o expresión regular) que aparece en el título de la pregunta
  //   columna:  encabezado de la columna del Excel de donde sale la respuesta
  //   valor:    respuesta fija (se usa en lugar de "columna")
  const REGLAS = [
    { pregunta: /es para/, valor: 'Negociar' },            // Punto 2: siempre "Negociar"
    { pregunta: /codigo/, columna: 'Código' },
    { pregunta: /nombre/, columna: 'Nombre del Cliente' },
    { pregunta: /jefatura/, columna: 'Jefatura' },
    { pregunta: /ruta/, columna: 'Ruta' },
    { pregunta: /canal/, columna: 'Descripción Canal' },
    { pregunta: /territorio/, columna: 'Territorio' },
  ];

  // Pausa (milisegundos) entre un envío y el siguiente.
  const PAUSA_ENTRE_ENVIOS = 1500;

  /* ===================================================================== */

  const KEY = 'autofill_forms_estado_v1';
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const norm = (s) =>
    String(s ?? '')
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/\s+/g, ' ')
      .trim();

  // ---------- Estado persistente (sobrevive a la recarga del formulario) ----------
  const cargarEstado = () => {
    try { return JSON.parse(GM_getValue(KEY, 'null')); } catch (e) { return null; }
  };
  const guardarEstado = (st) => GM_setValue(KEY, JSON.stringify(st));
  const borrarEstado = () => GM_deleteValue(KEY);

  // ---------- Lectura del Excel ----------
  function leerExcel(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onerror = () => reject(fr.error);
      fr.onload = () => {
        try {
          const wb = XLSX.read(new Uint8Array(fr.result), { type: 'array' });
          const ws = wb.Sheets[wb.SheetNames[0]];
          const matriz = XLSX.utils.sheet_to_json(ws, { header: 1, raw: false, defval: '' });
          const headers = (matriz[0] || []).map((h) => String(h).trim());
          const filas = matriz
            .slice(1)
            .filter((f) => f.some((c) => String(c).trim() !== ''))
            .map((f) => {
              const o = {};
              headers.forEach((h, i) => { o[h] = String(f[i] ?? '').trim(); });
              return o;
            });
          resolve({ headers, filas });
        } catch (e) { reject(e); }
      };
      fr.readAsArrayBuffer(file);
    });
  }

  // ---------- Lectura del formulario ----------
  function preguntasVisibles() {
    let items = [...document.querySelectorAll('[data-automation-id="questionItem"]')];
    if (!items.length) items = [...document.querySelectorAll('.office-form-question')];
    return items.filter((el) => el.offsetParent !== null);
  }

  function tituloDe(q) {
    const t =
      q.querySelector('[data-automation-id="questionTitle"]') ||
      q.querySelector('.office-form-question-title') ||
      q.querySelector('[role="heading"]');
    let s = norm(t ? t.textContent : q.textContent.slice(0, 200));
    return s.replace(/^\d+\s*[.)-]?\s*/, '').replace(/\*/g, '').trim();
  }

  function esObligatoria(q) {
    return !!(q.querySelector('[aria-required="true"], [data-automation-id="requiredStar"]') ||
      /\*/.test((q.querySelector('[data-automation-id="questionTitle"]') || {}).textContent || ''));
  }

  function respuestaPara(titulo, fila, headers) {
    for (const r of REGLAS) {
      const hit = r.pregunta instanceof RegExp ? r.pregunta.test(titulo) : titulo.includes(norm(r.pregunta));
      if (!hit) continue;
      if ('valor' in r) return { valor: r.valor, origen: 'fijo' };
      const col = headers.find((h) => norm(h) === norm(r.columna));
      if (col) return { valor: fila[col], origen: col };
    }
    // Respaldo: el título contiene el nombre de una columna del Excel.
    const col = headers.find((h) => norm(h) && titulo.includes(norm(h)));
    if (col) return { valor: fila[col], origen: col };
    return null;
  }

  // ---------- Escritura en el formulario ----------
  function escribirTexto(el, valor) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, valor);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }

  function mejorOpcion(opciones, valor) {
    // opciones: [{texto, el}]
    const v = norm(valor);
    return (
      opciones.find((o) => norm(o.texto) === v) ||
      opciones.find((o) => norm(o.texto).startsWith(v) || v.startsWith(norm(o.texto))) ||
      opciones.find((o) => norm(o.texto).includes(v) || v.includes(norm(o.texto)))
    );
  }

  async function responder(q, valor) {
    // 1) Opción única / múltiple
    const choices = [...q.querySelectorAll('input[type="radio"], input[type="checkbox"]')];
    if (choices.length) {
      const opciones = choices.map((el) => {
        const lab = el.closest('label') || el.closest('[data-automation-id="choiceItem"]') || el.parentElement;
        return { texto: el.value || (lab ? lab.textContent : ''), el };
      });
      const o = mejorOpcion(opciones, valor);
      if (!o) throw new Error(`no existe la opción "${valor}"`);
      if (!o.el.checked) o.el.click();
      return;
    }
    // 2) Lista desplegable
    const dd = q.querySelector('[aria-haspopup="listbox"], [role="combobox"]');
    if (dd) {
      dd.click();
      let lista = [];
      for (let i = 0; i < 30 && !lista.length; i++) {
        await sleep(100);
        lista = [...document.querySelectorAll('[role="option"]')].filter((el) => el.offsetParent !== null);
      }
      const o = mejorOpcion(lista.map((el) => ({ texto: el.textContent, el })), valor);
      if (!o) { document.body.click(); throw new Error(`no existe la opción "${valor}" en la lista`); }
      o.el.click();
      await sleep(150);
      return;
    }
    // 3) Texto
    const txt = q.querySelector('input[type="text"], input:not([type]), input[type="number"], textarea, input[data-automation-id="textInput"]');
    if (txt) { escribirTexto(txt, valor); return; }
    throw new Error('tipo de pregunta no reconocido');
  }

  async function esperar(fn, ms = 20000) {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      const r = fn();
      if (r) return r;
      await sleep(200);
    }
    return null;
  }

  const boton = (id, re) =>
    document.querySelector(`button[data-automation-id="${id}"]`) ||
    [...document.querySelectorAll('button')].find((b) => b.offsetParent !== null && re.test(norm(b.textContent)));

  // Llena todas las páginas del formulario con una fila. Devuelve el detalle.
  async function llenarFila(fila, headers) {
    const detalle = [];
    for (let pagina = 0; pagina < 20; pagina++) {
      const qs = await esperar(() => { const x = preguntasVisibles(); return x.length ? x : null; });
      if (!qs) throw new Error('no se encontraron preguntas en la página');
      for (const q of qs) {
        const titulo = tituloDe(q);
        const r = respuestaPara(titulo, fila, headers);
        if (!r) {
          if (esObligatoria(q)) throw new Error(`pregunta obligatoria sin regla: "${titulo}"`);
          detalle.push(`— "${titulo}": (se deja vacía)`);
          continue;
        }
        await responder(q, r.valor).catch((e) => { throw new Error(`"${titulo}": ${e.message}`); });
        detalle.push(`✔ "${titulo}" ← ${r.valor}  [${r.origen}]`);
      }
      const sig = boton('nextButton', /^(siguiente|next)$/);
      if (!sig) break;
      sig.click();
      await sleep(800);
    }
    return detalle;
  }

  async function enviarYConfirmar() {
    const enviar = boton('submitButton', /^(enviar|submit)$/);
    if (!enviar) throw new Error('no se encontró el botón Enviar');
    enviar.click();
    const ok = await esperar(() => {
      if (document.querySelector('[data-automation-id="thankYouMessage"], [data-automation-id="submitAnother"]')) return true;
      const t = norm(document.body.innerText);
      if (/(se (ha )?registr|se (ha )?envi|gracias|your response was submitted|thanks)/.test(t) && !boton('submitButton', /^(enviar|submit)$/)) return true;
      const err = document.querySelector('[data-automation-id="validationError"], [role="alert"]');
      if (err && err.offsetParent !== null && norm(err.textContent)) throw new Error('el formulario marcó un error: ' + err.textContent.trim());
      return false;
    }, 30000);
    if (!ok) throw new Error('no se confirmó el envío (30 s)');
  }

  // ---------- Panel ----------
  let panel, $;
  function crearPanel() {
    panel = document.createElement('div');
    panel.id = 'af-panel';
    panel.innerHTML = `
      <style>
        #af-panel{position:fixed;right:16px;bottom:16px;z-index:2147483647;width:340px;max-height:80vh;overflow:auto;
          background:#fff;color:#1b1b1b;border:1px solid #c8c8c8;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18);
          font:13px/1.4 Segoe UI,Arial,sans-serif;padding:12px}
        #af-panel h3{margin:0 0 8px;font-size:14px}
        #af-panel button{margin:4px 4px 0 0;padding:6px 10px;border-radius:6px;border:1px solid #0f6cbd;background:#0f6cbd;color:#fff;cursor:pointer}
        #af-panel button.sec{background:#fff;color:#0f6cbd}
        #af-panel button:disabled{opacity:.5;cursor:default}
        #af-panel input[type=number]{width:70px}
        #af-panel pre{white-space:pre-wrap;background:#f5f5f5;padding:6px;border-radius:6px;max-height:220px;overflow:auto;margin:8px 0 0;font-size:12px}
        #af-panel .min{float:right;background:none;border:none;color:#666;margin:0;padding:0 4px}
      </style>
      <button class="min" data-a="min" title="Minimizar">—</button>
      <h3>Autollenado desde Excel</h3>
      <div data-a="cuerpo">
        <input type="file" accept=".xlsx,.xls,.xlsm,.csv" data-a="file"><br>
        <div data-a="info" style="margin-top:6px;color:#555">Carga el Excel para empezar.</div>
        <div style="margin-top:6px">Empezar en la fila # <input type="number" min="1" value="1" data-a="desde"></div>
        <button data-a="probar" class="sec" disabled>Probar (llenar sin enviar)</button>
        <button data-a="iniciar" disabled>Enviar todas</button>
        <button data-a="pausar" class="sec" disabled>Pausar</button>
        <button data-a="reset" class="sec">Reiniciar</button>
        <pre data-a="log"></pre>
      </div>`;
    document.body.appendChild(panel);
    $ = (a) => panel.querySelector(`[data-a="${a}"]`);
    $('min').onclick = () => { const c = $('cuerpo'); c.style.display = c.style.display === 'none' ? '' : 'none'; };
  }

  function log(msg) { const p = $('log'); p.textContent = (msg + '\n' + p.textContent).slice(0, 20000); }
  function info(msg) { $('info').textContent = msg; }

  let datos = null; // {headers, filas}

  function refrescarBotones(st) {
    const hay = !!(datos || (st && st.filas));
    $('probar').disabled = !hay || (st && st.corriendo);
    $('iniciar').disabled = !hay || (st && st.corriendo);
    $('pausar').disabled = !(st && st.corriendo);
  }

  async function correr() {
    let st = cargarEstado();
    if (!st || !st.corriendo) return;
    refrescarBotones(st);
    while (st.corriendo && st.indice < st.filas.length) {
      const n = st.indice + 1;
      const fila = st.filas[st.indice];
      info(`Enviando fila ${n} de ${st.filas.length}: ${fila[st.headers[1]] || fila[st.headers[0]] || ''}`);
      try {
        const detalle = await llenarFila(fila, st.headers);
        if (st.soloPrueba) {
          st.corriendo = false; st.soloPrueba = false; guardarEstado(st);
          log(`PRUEBA fila ${n} (NO enviada). Revisa el formulario:\n` + detalle.join('\n'));
          info('Prueba lista. Si todo está bien, recarga la página y pulsa "Enviar todas".');
          refrescarBotones(st);
          return;
        }
        await enviarYConfirmar();
        st = cargarEstado() || st;
        st.indice++; st.enviadas = (st.enviadas || 0) + 1;
        guardarEstado(st);
        log(`✔ Fila ${n} enviada`);
      } catch (e) {
        st = cargarEstado() || st;
        st.corriendo = false; guardarEstado(st);
        log(`✖ Fila ${n}: ${e.message}`);
        info(`Detenido en la fila ${n}. Corrige y pulsa "Enviar todas" para continuar desde ahí.`);
        $('desde').value = n;
        refrescarBotones(st);
        return;
      }
      if (!st.corriendo) break;
      if (st.indice >= st.filas.length) break;
      await sleep(PAUSA_ENTRE_ENVIOS);
      // Abrir el formulario en blanco para la siguiente fila (el script continúa solo al recargar).
      location.href = st.url;
      return;
    }
    if (st.indice >= st.filas.length) {
      st.corriendo = false; guardarEstado(st);
      info(`✅ Terminado: ${st.enviadas || 0} respuestas enviadas.`);
      log('Proceso completo.');
      refrescarBotones(st);
    } else {
      info(`En pausa en la fila ${st.indice + 1}.`);
      $('desde').value = st.indice + 1;
      refrescarBotones(st);
    }
  }

  function arrancar(soloPrueba) {
    const prev = cargarEstado();
    const base = datos || (prev && prev.filas ? prev : null);
    if (!base) return;
    const desde = Math.max(1, parseInt($('desde').value, 10) || 1) - 1;
    if (desde >= base.filas.length) { info('La fila inicial es mayor que el total de filas.'); return; }
    if (!soloPrueba && !confirm(`Se enviarán ${base.filas.length - desde} respuestas (filas ${desde + 1} a ${base.filas.length}). ¿Continuar?`)) return;
    const st = {
      headers: base.headers, filas: base.filas, indice: desde,
      enviadas: (prev && prev.enviadas) || 0,
      corriendo: true, soloPrueba, url: location.href.split('#')[0],
    };
    guardarEstado(st);
    if (soloPrueba) correr();
    else if (preguntasVisibles().length && !preguntaYaTocada()) correr();
    else location.href = st.url;
  }

  // Si el formulario visible ya fue llenado (p. ej. tras una prueba), se recarga antes de enviar.
  let tocado = false;
  const preguntaYaTocada = () => tocado;

  function init() {
    if (document.getElementById('af-panel')) return;
    crearPanel();
    const st = cargarEstado();
    if (st && st.filas) {
      info(`Excel en memoria: ${st.filas.length} filas. Siguiente: fila ${st.indice + 1}.`);
      $('desde').value = Math.min(st.indice + 1, st.filas.length);
    }
    refrescarBotones(st);

    $('file').onchange = async (ev) => {
      const f = ev.target.files[0];
      if (!f) return;
      try {
        datos = await leerExcel(f);
        const faltan = REGLAS.filter((r) => r.columna && !datos.headers.some((h) => norm(h) === norm(r.columna)))
          .map((r) => r.columna);
        info(`${f.name}: ${datos.filas.length} filas. Columnas: ${datos.headers.join(', ')}` +
          (faltan.length ? `\n⚠ Faltan columnas: ${faltan.join(', ')}` : ''));
        $('desde').value = 1;
        guardarEstado({ headers: datos.headers, filas: datos.filas, indice: 0, enviadas: 0, corriendo: false });
        datos = cargarEstado();
        refrescarBotones(cargarEstado());
      } catch (e) {
        info('No se pudo leer el Excel: ' + e.message);
      }
    };
    $('probar').onclick = () => { tocado = true; arrancar(true); };
    $('iniciar').onclick = () => arrancar(false);
    $('pausar').onclick = () => {
      const s = cargarEstado(); if (!s) return;
      s.corriendo = false; guardarEstado(s);
      info('Pausando… (se detiene después del envío en curso)');
      refrescarBotones(s);
    };
    $('reset').onclick = () => {
      if (!confirm('¿Borrar el Excel cargado y el progreso?')) return;
      const s = cargarEstado();
      if (s && s.corriendo) { s.corriendo = false; guardarEstado(s); }
      borrarEstado(); datos = null; $('file').value = ''; $('log').textContent = '';
      info('Carga el Excel para empezar.'); $('desde').value = 1; refrescarBotones(null);
    };

    if (st && st.corriendo) correr();
  }

  // Esperar a que el formulario cargue (es una app que se dibuja con JavaScript).
  (async () => {
    await esperar(() => document.body && (preguntasVisibles().length || document.querySelector('button')), 30000);
    init();
  })();
})();
