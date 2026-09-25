// ==UserScript==
// @name         Autollenado Microsoft Forms desde Excel (ML rutas)
// @namespace    https://github.com/felipebarv25
// @version      1.4.2
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
  //   exacta:   true = pregunta FILTRO: si el valor del Excel no corresponde a
  //             ninguna opción del formulario (tolerando errores de escritura,
  //             ver SIMILITUD_MINIMA), esa fila NO se envía y se pasa a la siguiente.
  const REGLAS = [
    { pregunta: /es para/, valor: 'Negociar' },            // Punto 2: siempre "Negociar"
    { pregunta: /codigo/, columna: 'Código' },
    { pregunta: /nombre/, columna: 'Nombre del Cliente' },
    { pregunta: /jefatura/, columna: 'Jefatura' },
    { pregunta: /ruta/, columna: 'Ruta' },
    { pregunta: /canal/, columna: 'Descripción Canal', exacta: true }, // Subcanal
    { pregunta: /territorio/, columna: 'Territorio' },
  ];

  // Qué tan parecido debe ser un texto para aceptar un error de escritura
  // (0 a 1). 0.85 = se tolera más o menos 1 letra mala por cada 7.
  //   "COMIDAS RAPIDAS" vs "OMIDAS RAPIDAS" → 0.93 ✔ (se interpreta)
  //   "DROGUERIA"       vs "DROGUERIA HM"   → 0.75 ✘ (es otro canal)
  const SIMILITUD_MINIMA = 0.85;

  // Equivalencias manuales: "lo que dice el Excel": "lo que dice el formulario".
  // Úsalas para casos que la interpretación automática no resuelve sola.
  // No importan mayúsculas ni tildes. Ejemplo:
  //   'MINIMERCADOS': 'MINI MERCADO',
  const EQUIVALENCIAS = {
    'REST COMIDA RAPIDA KA': 'OMIDAS RAPIDAS',
    'HELADERIAS KA': 'FRUTERIA / HELADERIA',
  };

  // En "Validar canales" y en el resumen se avisa de los canales omitidos cuya opción
  // más parecida supera este valor, por si son el mismo canal y conviene agregar una
  // equivalencia (así no se salta ninguna fila que sí pertenezca).
  const AVISO_DUDOSO = 0.6;

  // Pausa (milisegundos) entre un envío y el siguiente.
  const PAUSA_ENTRE_ENVIOS = 1500;

  /* ===================================================================== */

  const KEY = 'autofill_forms_estado_v1';
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const norm = (s) =>
    String(s ?? '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
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

  // Texto de accesibilidad que Forms agrega al título ("Texto de varias líneas.", "Opción única.", ...).
  const SUFIJOS_TIPO = /(texto de (varias lineas|una sola linea)|eleccion multiple|opcion unica|opcion multiple|lista desplegable|calificacion|fecha|numero|multi-?line text|single line text|multiple choice|single choice|drop-?down|rating|date)\.?\s*$/;

  function tituloDe(q) {
    const t =
      q.querySelector('[data-automation-id="questionTitle"] .text-format-content') ||
      q.querySelector('[data-automation-id="questionTitle"]') ||
      q.querySelector('.office-form-question-title') ||
      q.querySelector('[role="heading"]');
    let s = norm(t ? t.textContent : q.textContent.slice(0, 200));
    s = s.replace(/^\d+\s*[.)-]?\s*/, '').replace(/\*/g, '').trim();
    for (let i = 0; i < 2; i++) { const sin = s.replace(SUFIJOS_TIPO, '').trim(); if (sin) s = sin; }
    return s.replace(/[:.]\s*$/, '').trim();
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
      if (col) return { valor: fila[col], origen: col, exacta: !!r.exacta };
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

  // Largo con el que Excel suele cortar las descripciones (p. ej. "INSTITUCIONES Y OFICINAS (ENTIDADES PRIV").
  const LARGO_CORTADO = 30;

  // Texto comparable: sin tildes, sin mayúsculas, sin signos ("BAR / DISCOTECA" = "bar discoteca").
  const limpiar = (s) => norm(s).replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();

  // Distancia de edición (Levenshtein): letras a cambiar, quitar o agregar para igualar a y b.
  function distancia(a, b) {
    let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      for (let j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      }
      prev = cur;
    }
    return prev[b.length];
  }
  const similitud = (a, b) => 1 - distancia(a, b) / Math.max(a.length, b.length, 1);

  const EQUIV = Object.fromEntries(Object.entries(EQUIVALENCIAS).map(([k, v]) => [limpiar(k), v]));

  // Busca la opción del formulario que corresponde al valor del Excel.
  // Devuelve {opcion, tipo, parecido} o null. tipo: exacta | equivalencia | cortada | interpretada
  function buscarOpcion(opciones, valor) {
    let v = limpiar(valor);
    let tipo = 'exacta';
    if (EQUIV[v]) { v = limpiar(EQUIV[v]); tipo = 'equivalencia'; }
    // Cada opción puede tener varios textos (etiqueta, aria-label, value): se prueban todos.
    const ops = [];
    opciones.forEach((o) => (o.textos || [o.texto]).forEach((t) => { if (limpiar(t)) ops.push({ o, t: limpiar(t) }); }));

    const igual = ops.find((x) => x.t === v);
    if (igual) return { opcion: igual.o, tipo, parecido: 1 };

    // Nombre cortado por Excel: solo una opción empieza igual.
    if (v.length >= LARGO_CORTADO) {
      const empiezan = ops.filter((x) => x.t.startsWith(v));
      if (empiezan.length === 1) return { opcion: empiezan[0].o, tipo: 'cortada', parecido: 1 };
    }

    // Error de escritura (en el Excel o en el formulario).
    const puntajes = ops
      .map((x) => {
        let p = similitud(v, x.t);
        if (v.length >= LARGO_CORTADO && x.t.length > v.length) p = Math.max(p, similitud(v, x.t.slice(0, v.length)));
        return { ...x, p };
      })
      .sort((a, b) => b.p - a.p);
    const mejor = puntajes[0];
    const segundo = mejor && puntajes.find((x) => x.o !== mejor.o);
    if (mejor && mejor.p >= SIMILITUD_MINIMA && (!segundo || mejor.p - segundo.p >= 0.05)) {
      return { opcion: mejor.o, tipo: tipo === 'equivalencia' ? 'equivalencia' : 'interpretada', parecido: mejor.p };
    }
    return null;
  }

  // La opción más parecida (para explicar por qué no se marcó nada).
  function cercana(opciones, valor) {
    const v = limpiar(valor);
    let best = null;
    opciones.forEach((o) => (o.textos || [o.texto]).forEach((t) => {
      const p = similitud(v, limpiar(t));
      if (!best || p > best.p) best = { texto: o.texto.trim(), p };
    }));
    return best;
  }
  function masCercana(opciones, valor) {
    const best = cercana(opciones, valor);
    if (!best) return 'el formulario no mostró opciones';
    return `más parecida: "${best.texto}" (${Math.round(best.p * 100)}%)` +
      (best.p >= AVISO_DUDOSO ? ' ⚠ REVISA: ¿es el mismo canal? agrégalo en EQUIVALENCIAS' : '');
  }

  function mejorOpcion(opciones, valor, exacta) {
    const r = buscarOpcion(opciones, valor);
    if (r || exacta) return r;
    // Preguntas que no son el subcanal: se permite además que un texto contenga al otro.
    const v = norm(valor);
    const o =
      opciones.find((o) => norm(o.texto).startsWith(v) || v.startsWith(norm(o.texto))) ||
      opciones.find((o) => norm(o.texto).includes(v) || v.includes(norm(o.texto)));
    return o ? { opcion: o, tipo: 'aproximada', parecido: 0 } : null;
  }

  const describir = (valor, m) =>
    m.tipo === 'exacta' ? valor
      : `${valor} → "${m.opcion.texto.trim()}" (${m.tipo}${m.tipo === 'interpretada' ? ' ' + Math.round(m.parecido * 100) + '%' : ''})`;

  class OpcionNoExiste extends Error {}
  class FilaOmitida extends Error {}

  // Revisa las preguntas filtro (subcanal) ANTES de llenar nada.
  // Devuelve null si la fila se puede enviar, o el motivo para omitirla.
  async function motivoParaOmitir(fila, headers) {
    const qs = preguntasVisibles();
    for (const regla of REGLAS.filter((r) => r.exacta && r.columna)) {
      const col = headers.find((h) => norm(h) === norm(regla.columna));
      const valor = col ? String(fila[col] || '').trim() : '';
      if (!valor) return `"${regla.columna}" está vacío en el Excel`;
      const q = qs.find((x) => respuestaPara(tituloDe(x), fila, headers)?.exacta);
      if (!q) continue; // la pregunta está en otra página: se revisa al llegar a ella
      const sel = await leerOpciones(q);
      if (sel && sel.tipo === 'lista') { cerrarLista(); await sleep(200); }
      if (!sel || !sel.opciones.length) throw new Error(`no se pudieron leer las opciones de "${tituloDe(q)}"`);
      if (!buscarOpcion(sel.opciones, valor)) return `canal "${valor}" no está en el formulario (${masCercana(sel.opciones, valor)})`;
    }
    return null;
  }

  function cerrarLista() {
    const ev = { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true };
    (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', ev));
    document.body.dispatchEvent(new KeyboardEvent('keydown', ev));
  }

  // Lee las opciones de una pregunta de selección. En listas desplegables la deja abierta.
  async function leerOpciones(q) {
    const choices = [...q.querySelectorAll('input[type="radio"], input[type="checkbox"]')];
    if (choices.length) {
      return {
        tipo: 'opciones',
        opciones: choices.map((el) => {
          const lab = el.closest('[data-automation-id="choiceItem"]') || el.closest('label') || el.parentElement;
          const textos = [lab && lab.textContent, el.getAttribute('aria-label'), el.value]
            .map((t) => String(t || '').trim())
            .filter((t, i, a) => t && t !== 'on' && a.indexOf(t) === i);
          return { texto: textos[0] || '', textos, el };
        }),
      };
    }
    const dd = q.querySelector('[aria-haspopup="listbox"], [role="combobox"]');
    if (dd) {
      const antes = new Set(document.querySelectorAll('[role="listbox"]'));
      dd.click();
      // Solo las opciones de ESTA lista: la que indica aria-controls, o la que se acaba de abrir.
      const listaPropia = () => {
        const id = dd.getAttribute('aria-controls') || dd.getAttribute('aria-owns');
        const porId = id && document.getElementById(id);
        if (porId && porId.offsetParent !== null) return porId;
        const nuevas = [...document.querySelectorAll('[role="listbox"]')].filter((l) => !antes.has(l) && l.offsetParent !== null);
        if (nuevas.length) return nuevas[nuevas.length - 1];
        return q.querySelector('[role="listbox"]');
      };
      let lista = [];
      for (let i = 0; i < 30 && !lista.length; i++) {
        await sleep(100);
        const lb = listaPropia();
        lista = lb ? [...lb.querySelectorAll('[role="option"]')].filter((el) => el.offsetParent !== null) : [];
      }
      return { tipo: 'lista', opciones: lista.map((el) => ({ texto: el.textContent, el })) };
    }
    return null;
  }

  // Responde una pregunta. Devuelve la opción elegida (o null si es de texto).
  async function responder(q, valor, exacta) {
    const sel = await leerOpciones(q);
    if (sel) {
      const m = mejorOpcion(sel.opciones, valor, exacta);
      if (!m) {
        if (sel.tipo === 'lista') { cerrarLista(); await sleep(200); }
        throw new OpcionNoExiste(`no existe la opción "${valor}" — ${masCercana(sel.opciones, valor)}`);
      }
      if (!(m.opcion.el.checked)) m.opcion.el.click();
      await sleep(150);
      return m;
    }
    const txt = q.querySelector('input[type="text"], input:not([type]), input[type="number"], textarea, input[data-automation-id="textInput"]');
    if (txt) { escribirTexto(txt, valor); return null; }
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
        if (r.exacta && !String(r.valor).trim()) throw new FilaOmitida(`"${titulo}" está vacío en el Excel`);
        let m;
        try {
          m = await responder(q, r.valor, r.exacta);
        } catch (e) {
          if (r.exacta && e instanceof OpcionNoExiste) {
            throw new FilaOmitida(`canal "${r.valor}" no está en el formulario (${e.message.split(' — ')[1] || ''})`);
          }
          throw new Error(`"${titulo}": ${e.message}`);
        }
        detalle.push(`✔ "${titulo}" ← ${m ? describir(r.valor, m) : r.valor}  [${r.origen}]`);
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
      <h3>Autollenado desde Excel <small style="color:#888;font-weight:normal">v${(typeof GM_info !== 'undefined' && GM_info.script && GM_info.script.version) || ''}</small></h3>
      <div data-a="cuerpo">
        <input type="file" accept=".xlsx,.xls,.xlsm,.csv" data-a="file"><br>
        <div data-a="info" style="margin-top:6px;color:#555">Carga el Excel para empezar.</div>
        <div style="margin-top:6px">Empezar en la fila # <input type="number" min="1" value="1" data-a="desde"></div>
        <button data-a="probar" class="sec" disabled>Probar (llenar sin enviar)</button>
        <button data-a="iniciar" disabled>Enviar todas</button>
        <button data-a="pausar" class="sec" disabled>Pausar</button>
        <button data-a="validar" class="sec" disabled>Validar canales</button>
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
    $('validar').disabled = !hay || (st && st.corriendo);
  }

  async function correr() {
    let st = cargarEstado();
    if (!st || !st.corriendo) return;
    refrescarBotones(st);
    while (st.corriendo && st.indice < st.filas.length) {
      const n = st.indice + 1;
      const fila = st.filas[st.indice];
      const nombre = fila[st.headers[1]] || fila[st.headers[0]] || '';
      info(`Fila ${n} de ${st.filas.length}: ${nombre}`);
      const omitir = (motivo) => {
        st = cargarEstado() || st;
        st.indice++;
        st.omitidas = (st.omitidas || []).filter((x) => !x.startsWith(`Fila ${n} (`)).concat(`Fila ${n} (${nombre}): ${motivo}`);
        guardarEstado(st);
        log(`⏭ Fila ${n} OMITIDA: ${motivo}`);
      };
      try {
        const motivo = await motivoParaOmitir(fila, st.headers);
        if (motivo) { omitir(motivo); continue; } // nada se llenó: sigue en la misma página
      } catch (e) {
        st = cargarEstado() || st;
        st.corriendo = false; guardarEstado(st);
        log(`✖ Fila ${n}: ${e.message}`);
        info(`Detenido en la fila ${n}.`);
        refrescarBotones(st);
        return;
      }
      try {
        const detalle = await llenarFila(fila, st.headers);
        if (st.soloPrueba) {
          st.corriendo = false; st.soloPrueba = false; guardarEstado(st);
          log(`PRUEBA fila ${n} (NO enviada). Revisa el formulario:\n` + detalle.join('\n'));
          st.indice = n - 1; guardarEstado(st); $('desde').value = n;
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
        if (e instanceof FilaOmitida) {
          // Pasa solo si la pregunta filtro estaba en otra página: el formulario quedó a medio llenar.
          omitir(e.message);
          if (st.indice >= st.filas.length) break;
          location.href = st.url;
          return;
        }
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
      const om = st.omitidas || [];
      info(`✅ Terminado: ${st.enviadas || 0} enviadas, ${om.length} omitidas (canal no está en el formulario).`);
      $('log').textContent = `Proceso completo: ${st.enviadas || 0} enviadas, ${om.length} omitidas.` +
        (om.length ? `\n\nFILAS OMITIDAS (${om.length}):\n` + om.join('\n') : '');
      refrescarBotones(st);
    } else {
      info(`En pausa en la fila ${st.indice + 1}.`);
      $('desde').value = st.indice + 1;
      refrescarBotones(st);
    }
  }

  // Compara cada canal distinto del Excel con las opciones del formulario, sin llenar nada.
  async function validarCanales() {
    const st = cargarEstado();
    if (!st || !st.filas) return;
    const reglas = REGLAS.filter((r) => r.exacta && r.columna);
    const qs = preguntasVisibles();
    const salida = [];
    for (const regla of reglas) {
      const q = qs.find((x) => (regla.pregunta instanceof RegExp ? regla.pregunta.test(tituloDe(x)) : tituloDe(x).includes(norm(regla.pregunta))));
      const col = st.headers.find((h) => norm(h) === norm(regla.columna));
      if (!q || !col) { salida.push(`No se encontró la pregunta o la columna "${regla.columna}".`); continue; }
      const sel = await leerOpciones(q);
      if (sel && sel.tipo === 'lista') { cerrarLista(); await sleep(200); }
      if (!sel || !sel.opciones.length) { salida.push(`"${tituloDe(q)}" no tiene opciones legibles.`); continue; }
      const cuenta = {};
      st.filas.forEach((f) => { const v = f[col]; if (v) cuenta[v] = (cuenta[v] || 0) + 1; });
      const grupos = { exacta: [], otra: [], no: [] };
      let enviar = 0, omitir = 0;
      const vacias = st.filas.filter((f) => !String(f[col] || '').trim()).length;
      for (const [v, n] of Object.entries(cuenta).sort((a, b) => b[1] - a[1])) {
        const m = buscarOpcion(sel.opciones, v);
        if (!m) { omitir += n; grupos.no.push(`✘ ${v} (${n} filas) → NO se envían; ${masCercana(sel.opciones, v)}`); }
        else if (m.tipo === 'exacta') { enviar += n; grupos.exacta.push(`✔ ${v} (${n})`); }
        else { enviar += n; grupos.otra.push(`≈ ${describir(v, m)} (${n} filas)`); }
      }
      salida.push(`RESUMEN: se ENVIARÁN ${enviar} filas y se OMITIRÁN ${omitir + vacias} (de ${st.filas.length}).`,
        ...(vacias ? [`(${vacias} filas tienen "${col}" vacío y se omiten)`] : []),
        `Pregunta "${tituloDe(q)}" — ${sel.opciones.length} opciones en el formulario`,
        `\nINTERPRETADOS POR ERROR DE ESCRITURA (sí se envían; revisa que estén bien):`, ...(grupos.otra.length ? grupos.otra : ['(ninguno)']),
        `\nNO ESTÁN EN EL FORMULARIO (esas filas se omiten):`, ...(grupos.no.length ? grupos.no : ['(ninguno)']),
        `\nIGUALES (sí se envían):`, ...(grupos.exacta.length ? grupos.exacta : ['(ninguno)']),
        `\nOPCIONES DEL FORMULARIO (${sel.opciones.length}):`, ...sel.opciones.map((o, i) => `${i + 1}. ${(o.textos || [o.texto]).join('  |  ')}`));
    }
    $('log').textContent = salida.join('\n');
    info('Validación lista (no se llenó ni envió nada). Revisa el recuadro de abajo.');
  }

  function arrancar(soloPrueba) {
    const prev = cargarEstado();
    const base = datos || (prev && prev.filas ? prev : null);
    if (!base) return;
    const desde = Math.max(1, parseInt($('desde').value, 10) || 1) - 1;
    if (desde >= base.filas.length) { info('La fila inicial es mayor que el total de filas.'); return; }
    if (!soloPrueba && !confirm(`Se revisarán ${base.filas.length - desde} filas (de la ${desde + 1} a la ${base.filas.length}). ` +
      'Solo se enviarán las que tengan un canal que exista en el formulario; las demás se omiten. ¿Continuar?')) return;
    const st = {
      headers: base.headers, filas: base.filas, indice: desde,
      // Si se empieza desde la fila 1 es una corrida nueva: contadores en cero.
      enviadas: desde > 0 && prev ? prev.enviadas || 0 : 0,
      omitidas: desde > 0 && prev ? prev.omitidas || [] : [],
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
    $('validar').onclick = () => (tocado = true) && validarCanales().catch((e) => info('Error al validar: ' + e.message));
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
