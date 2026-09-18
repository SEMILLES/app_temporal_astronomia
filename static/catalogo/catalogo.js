"use strict";
const normalizarCatalogo=valor=>String(valor||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").replace(/[-_]+/g," ").replace(/\s+/g," ").trim().toLowerCase();
function coincideConcepto(datos,filtros){
  const normalizar=normalizarCatalogo,campos=JSON.parse(datos.semanticFields||"[]").map(normalizar);
  return (!filtros.consulta||normalizar(datos.conceptoBusqueda).includes(normalizar(filtros.consulta)))
    &&(!filtros.area||(datos.knowledgeAreas||"").split("||").map(normalizar).includes(filtros.area))
    &&(!filtros.video||datos.hasVideo==="true")
    &&(!filtros.variacion||datos.variationType===filtros.variacion)
    &&(!filtros.campos.length||filtros.campos.some(campo=>campos.includes(campo)));
}
if(typeof module!=="undefined")module.exports={coincideConcepto,normalizarCatalogo};
if(typeof document!=="undefined")document.addEventListener("DOMContentLoaded",()=>{
  const buscador=document.querySelector("#buscador-catalogo"),filtroVideo=document.querySelector("#filtro-video"),filtroArea=document.querySelector("#filtro-area"),filtroVariacion=document.querySelector("#filtro-variacion"),filtroCampos=document.querySelector("#filtro-campos"),buscadorCampos=document.querySelector("#buscador-campos");
  const conceptos=[...document.querySelectorAll("[data-concepto-busqueda]")],contador=document.querySelector("#contador-resultados"),normalizar=normalizarCatalogo;
  if(filtroArea){
    const areas=new Map();
    conceptos.forEach(elemento=>(elemento.dataset.knowledgeAreas||"").split("||").filter(Boolean).forEach(area=>areas.set(normalizar(area),area)));
    [...areas].sort((a,b)=>a[1].localeCompare(b[1],"es")).forEach(([value,label])=>filtroArea.add(new Option(label,value)));
  }
  if(filtroCampos){
    const campos=new Map();
    conceptos.forEach(elemento=>JSON.parse(elemento.dataset.semanticFields||"[]").forEach(campo=>campos.set(normalizar(campo),campo)));
    [...campos].sort((a,b)=>a[1].localeCompare(b[1],"es")).forEach(([value,label])=>{
      const fila=document.createElement("label"),casilla=document.createElement("input"),texto=document.createElement("span");
      casilla.type="checkbox";casilla.value=value;texto.textContent=label;
      fila.append(casilla,texto);filtroCampos.append(fila);
    });
  }
  const parametros=new URLSearchParams(location.search);
  if(buscador&&parametros.has("q"))buscador.value=parametros.get("q");
  if(filtroArea)filtroArea.value=parametros.get("area")||"";
  if(filtroVideo)filtroVideo.checked=parametros.get("video")==="1";
  if(filtroVariacion)filtroVariacion.value=parametros.get("variacion")||"";
  filtroCampos?.querySelectorAll("input").forEach(casilla=>casilla.checked=parametros.getAll("campo").includes(casilla.value));
  const estado=()=>({consulta:buscador?.value||"",area:filtroArea?.value||"",video:Boolean(filtroVideo?.checked),variacion:filtroVariacion?.value||"",campos:[...(filtroCampos?.querySelectorAll("input:checked")||[])].map(casilla=>casilla.value)});
  const consultaActual=()=>{
    const filtros=estado(),params=new URLSearchParams();
    if(filtros.consulta)params.set("q",filtros.consulta);
    if(filtros.area)params.set("area",filtros.area);
    if(filtros.video)params.set("video","1");
    if(filtros.variacion)params.set("variacion",filtros.variacion);
    filtros.campos.forEach(campo=>params.append("campo",campo));return params.toString();
  };
  const filtrar=()=>{
    const filtros=estado();let visibles=0;
    conceptos.forEach(elemento=>{const visible=coincideConcepto(elemento.dataset,filtros);elemento.classList.toggle("oculto",!visible);if(visible)visibles+=1});
    // Filter concepts only; every Alternative remains accessible.
    if(contador)contador.textContent=`${visibles} concepto(s)`;
    const query=consultaActual();
    document.querySelectorAll("[data-concepto-busqueda],[data-alternative-select]").forEach(enlace=>{const url=new URL(enlace.getAttribute("href"),location.href);url.search=query;enlace.setAttribute("href",url.href)});
  };
  const buscarCampos=()=>filtroCampos?.querySelectorAll("label").forEach(fila=>fila.hidden=!normalizar(fila.textContent).includes(normalizar(buscadorCampos?.value)));
  buscador?.addEventListener("input",filtrar);
  [filtroArea,filtroVideo,filtroVariacion,filtroCampos].forEach(control=>control?.addEventListener("change",filtrar));
  buscadorCampos?.addEventListener("input",buscarCampos);
  document.querySelector("#limpiar-filtros")?.addEventListener("click",()=>{
    if(buscador)buscador.value="";if(filtroArea)filtroArea.value="";if(filtroVideo)filtroVideo.checked=false;if(filtroVariacion)filtroVariacion.value="";
    filtroCampos?.querySelectorAll("input").forEach(casilla=>casilla.checked=false);
    if(buscadorCampos)buscadorCampos.value="";buscarCampos();filtrar();history.replaceState(null,"",location.pathname);
    // A direct server-side search may have returned a reduced concept list.
    if(parametros.has("q"))location.assign(location.pathname);
  });
  filtrar();
  const seleccionar=id=>{document.querySelectorAll("[data-alternative-select]").forEach(item=>item.classList.toggle("activo",item.dataset.alternativeSelect===id));document.querySelectorAll("[data-alternative-panel]").forEach(panel=>panel.hidden=panel.dataset.alternativePanel!==id);const enlace=document.querySelector(`.boton-variante[data-alternative-select="${CSS.escape(id)}"]`);if(enlace)history.replaceState(null,"",enlace.href)};
  document.querySelectorAll("[data-alternative-select]").forEach(item=>item.addEventListener("click",evento=>{evento.preventDefault();seleccionar(item.dataset.alternativeSelect)}));
  document.querySelectorAll("[data-pestana]").forEach(boton=>boton.addEventListener("click",()=>{const contenedor=boton.closest(".bloque");contenedor.querySelectorAll("[data-pestana]").forEach(item=>{const activo=item===boton;item.classList.toggle("activo",activo);item.setAttribute("aria-selected",String(activo))});contenedor.querySelectorAll("[data-panel]").forEach(panel=>panel.hidden=panel.dataset.panel!==boton.dataset.pestana)}));
});
