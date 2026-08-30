(() => {
  "use strict";

  const API = "/api/process-builder";
  const METADATA_PREFIX = "DEVREADY_PROCESS_METADATA:";
  const modeler = new BpmnJS({ container: "#canvas" });

  const blankDiagram = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions_DevReady" targetNamespace="https://devready.io/process-builder/bpmn">
  <bpmn:process id="Process_DevReady" name="Untitled client process" isExecutable="false">
    <bpmn:startEvent id="StartEvent_1" name="Start" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_DevReady">
    <bpmndi:BPMNPlane id="BPMNPlane_DevReady" bpmnElement="Process_DevReady">
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1"><dc:Bounds x="170" y="180" width="36" height="36" /></bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;

  const state = {
    processes: [],
    components: [],
    activeProcess: null,
    selectedElement: null,
    importing: false,
    dirty: false,
    chatHistory: [],
    drafts: [],
    validation: null,
    clientChecks: {},
    libraryTab: "processes",
    toolTab: "intake",
    toastTimer: null,
  };

  const ids = [
    "saveState", "aiStatus", "mcpStatus", "newProcess", "importProcess", "fileInput", "saveProcess", "validateProcess",
    "undo", "redo", "zoomOut", "zoomIn", "fitDiagram", "exportBpmn", "exportSvg", "processCount", "componentCount",
    "librarySearch", "processLibrary", "foundryLibrary", "createComponent", "diagramTitle", "processStatus", "diagramSubtitle",
    "canvas", "canvasLoading", "canvasError", "clientName", "chatConversation", "draftProcesses", "chatForm", "chatPrompt",
    "sendChat", "clearChat", "processForm", "processName", "processOwner", "processPurpose", "processScope", "processTrigger",
    "processOutcome", "processInputs", "processOutputs", "processSystems", "processControls", "processKpis", "selectedElementTitle",
    "elementEmpty", "elementForm", "elementName", "elementOwner", "elementSystem", "elementDescription", "elementControl", "elementSla",
    "elementComponent", "elementCodeRefs", "elementApiEndpoints", "elementMcpTools", "elementLinks", "refreshTrace", "traceList",
    "qualityScore", "runValidation", "validationSummary", "validationIssues", "clientChecklist", "markValidated", "componentDialog",
    "componentForm", "componentDialogTitle", "componentId", "componentName", "componentKind", "componentStatus", "componentDescription",
    "componentAliases", "componentCodeRefs", "componentApiEndpoints", "componentMcpTools", "componentLinks", "saveComponent", "deleteComponent", "toast",
  ];
  const el = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

  const escapeXml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");

  const lines = (value) => String(value || "")
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter((item, index, all) => item && all.findIndex((other) => other.toLowerCase() === item.toLowerCase()) === index);

  const lineText = (value) => Array.isArray(value) ? value.join("\n") : String(value || "");

  const safeFilename = (value, extension) => {
    const base = String(value || "devready-process")
      .trim()
      .replace(/[^a-z0-9._-]+/gi, "-")
      .replace(/^-+|-+$/g, "") || "devready-process";
    return `${base}.${extension}`;
  };

  const currentDomain = () => sessionStorage.getItem("domain") || "dev";

  const fetchJson = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: {
        "Cache-Control": "no-cache",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    let data = {};
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : (data.message || `HTTP ${response.status}`);
      throw new Error(detail);
    }
    return data;
  };

  const toast = (message, kind = "") => {
    window.clearTimeout(state.toastTimer);
    el.toast.textContent = message;
    el.toast.className = `toast${kind ? ` ${kind}` : ""}`;
    el.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => { el.toast.hidden = true; }, 5200);
  };

  const showCanvasError = (message = "") => {
    el.canvasError.textContent = message;
    el.canvasError.hidden = !message;
    if (message) el.canvasLoading.hidden = true;
  };

  const setDirty = (dirty = true) => {
    state.dirty = dirty;
    el.saveState.className = `status-chip ${dirty ? "checking" : (state.activeProcess ? "saved" : "neutral")}`;
    el.saveState.textContent = dirty ? "Unsaved changes" : (state.activeProcess ? "Saved" : "Not saved");
  };

  const confirmDiscard = () => !state.dirty || window.confirm("Discard the unsaved changes on this process canvas?");

  const download = (content, type, filename) => {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const readDocumentation = (businessObject) => {
    const result = { text: "", metadata: {} };
    for (const item of businessObject?.documentation || []) {
      const value = String(item?.text || "");
      if (value.startsWith(METADATA_PREFIX)) {
        try { result.metadata = JSON.parse(value.slice(METADATA_PREFIX.length)); } catch { result.metadata = {}; }
      } else if (!result.text && value.trim()) {
        result.text = value;
      }
    }
    return result;
  };

  const documentationValues = (text, metadata = {}) => {
    const factory = modeler.get("bpmnFactory");
    const values = [];
    if (String(text || "").trim()) values.push(factory.create("bpmn:Documentation", { text: String(text).trim() }));
    const compact = Object.fromEntries(Object.entries(metadata).filter(([, value]) => Array.isArray(value) ? value.length : String(value || "").trim()));
    if (Object.keys(compact).length) {
      values.push(factory.create("bpmn:Documentation", { text: `${METADATA_PREFIX}${JSON.stringify(compact)}` }));
    }
    return values;
  };

  const rootBusinessObject = () => {
    const root = modeler.get("canvas").getRootElement()?.businessObject;
    if (root?.$type === "bpmn:Process") return root;
    if (root?.$type === "bpmn:Collaboration") return root.participants?.[0]?.processRef || root;
    return root;
  };

  const processMetadataFromForm = () => ({
    clientName: el.clientName.value.trim(),
    owner: el.processOwner.value.trim(),
    purpose: el.processPurpose.value.trim(),
    scope: el.processScope.value.trim(),
    trigger: el.processTrigger.value.trim(),
    outcome: el.processOutcome.value.trim(),
    inputs: lines(el.processInputs.value),
    outputs: lines(el.processOutputs.value),
    systems: lines(el.processSystems.value),
    controls: lines(el.processControls.value),
    kpis: lines(el.processKpis.value),
  });

  const applyProcessFormToModel = () => {
    const root = modeler.get("canvas").getRootElement();
    if (!root) return;
    const name = el.processName.value.trim() || "Untitled client process";
    modeler.get("modeling").updateProperties(root, {
      name,
      documentation: documentationValues(el.processPurpose.value, processMetadataFromForm()),
    });
    el.diagramTitle.textContent = name;
    setDirty(true);
  };

  const activeElementRecord = (elementId) => state.activeProcess?.elements?.find((item) => item.id === elementId) || null;

  const elementMetadata = (shape) => {
    const documented = readDocumentation(shape?.businessObject);
    const saved = activeElementRecord(shape?.id) || {};
    return {
      text: documented.text || saved.description || "",
      metadata: {
        owner: documented.metadata.owner || saved.owner || "",
        system: documented.metadata.system || saved.system || "",
        control: documented.metadata.control || saved.control || "",
        sla: documented.metadata.sla || saved.sla || "",
        componentId: documented.metadata.componentId || saved.component_id || "",
        codeRefs: documented.metadata.codeRefs || saved.code_refs || [],
        apiEndpoints: documented.metadata.apiEndpoints || saved.api_endpoints || [],
        mcpTools: documented.metadata.mcpTools || saved.mcp_tools || [],
        links: documented.metadata.links || saved.links || [],
      },
    };
  };

  const populateProcessForm = () => {
    const documented = readDocumentation(rootBusinessObject());
    const process = state.activeProcess || {};
    const metadata = documented.metadata || {};
    el.processName.value = process.name || rootBusinessObject()?.name || "Untitled client process";
    el.clientName.value = process.client_name || metadata.clientName || el.clientName.value || "";
    el.processOwner.value = process.owner || metadata.owner || "";
    el.processPurpose.value = process.purpose || documented.text || metadata.purpose || "";
    el.processScope.value = process.scope || metadata.scope || "";
    el.processTrigger.value = process.trigger || metadata.trigger || "";
    el.processOutcome.value = process.outcome || metadata.outcome || "";
    el.processInputs.value = lineText(process.inputs || metadata.inputs);
    el.processOutputs.value = lineText(process.outputs || metadata.outputs);
    el.processSystems.value = lineText(process.systems || metadata.systems);
    el.processControls.value = lineText(process.controls || metadata.controls);
    el.processKpis.value = lineText(process.kpis || metadata.kpis);
  };

  const resetElementForm = () => {
    state.selectedElement = null;
    el.selectedElementTitle.textContent = "No activity selected";
    el.elementEmpty.hidden = false;
    el.elementForm.hidden = true;
  };

  const populateComponentSelect = (selected = "") => {
    const first = document.createElement("option");
    first.value = "";
    first.textContent = "Check by activity name when saved";
    el.elementComponent.replaceChildren(first);
    for (const component of state.components) {
      const option = document.createElement("option");
      option.value = component.id;
      option.textContent = `${component.name} · ${component.status}`;
      option.selected = component.id === selected;
      el.elementComponent.appendChild(option);
    }
  };

  const populateElementForm = (shape) => {
    state.selectedElement = shape;
    const { text, metadata } = elementMetadata(shape);
    el.selectedElementTitle.textContent = shape.businessObject?.name || shape.id;
    el.elementEmpty.hidden = true;
    el.elementForm.hidden = false;
    el.elementName.value = shape.businessObject?.name || "";
    el.elementOwner.value = metadata.owner || "";
    el.elementSystem.value = metadata.system || "";
    el.elementDescription.value = text || "";
    el.elementControl.value = metadata.control || "";
    el.elementSla.value = metadata.sla || "";
    populateComponentSelect(metadata.componentId || "");
    el.elementCodeRefs.value = lineText(metadata.codeRefs);
    el.elementApiEndpoints.value = lineText(metadata.apiEndpoints);
    el.elementMcpTools.value = lineText(metadata.mcpTools);
    el.elementLinks.value = lineText(metadata.links);
  };

  const applyElementForm = () => {
    const shape = state.selectedElement;
    if (!shape) return;
    const metadata = {
      owner: el.elementOwner.value.trim(),
      system: el.elementSystem.value.trim(),
      control: el.elementControl.value.trim(),
      sla: el.elementSla.value.trim(),
      componentId: el.elementComponent.value,
      codeRefs: lines(el.elementCodeRefs.value),
      apiEndpoints: lines(el.elementApiEndpoints.value),
      mcpTools: lines(el.elementMcpTools.value),
      links: lines(el.elementLinks.value),
    };
    modeler.get("modeling").updateProperties(shape, {
      name: el.elementName.value.trim(),
      documentation: documentationValues(el.elementDescription.value, metadata),
    });
    populateElementForm(shape);
    setDirty(true);
    renderReferenceOverlays();
    renderTrace();
  };

  const bpmnTypeFromAi = (type) => ({
    start_event: "bpmn:StartEvent",
    end_event: "bpmn:EndEvent",
    user_task: "bpmn:UserTask",
    service_task: "bpmn:ServiceTask",
    manual_task: "bpmn:ManualTask",
    business_rule_task: "bpmn:BusinessRuleTask",
    send_task: "bpmn:SendTask",
    receive_task: "bpmn:ReceiveTask",
    call_activity: "bpmn:CallActivity",
    decision: "bpmn:ExclusiveGateway",
    parallel_gateway: "bpmn:ParallelGateway",
    task: "bpmn:Task",
  }[type] || "bpmn:Task");

  const xmlTagForType = (type) => ({
    "bpmn:StartEvent": "startEvent",
    "bpmn:EndEvent": "endEvent",
    "bpmn:UserTask": "userTask",
    "bpmn:ServiceTask": "serviceTask",
    "bpmn:ManualTask": "manualTask",
    "bpmn:BusinessRuleTask": "businessRuleTask",
    "bpmn:SendTask": "sendTask",
    "bpmn:ReceiveTask": "receiveTask",
    "bpmn:CallActivity": "callActivity",
    "bpmn:ExclusiveGateway": "exclusiveGateway",
    "bpmn:ParallelGateway": "parallelGateway",
    "bpmn:Task": "task",
  }[type] || "task");

  const validBpmnId = (value, prefix, used) => {
    let candidate = String(value || "").trim().replace(/[^A-Za-z0-9_.:-]/g, "_");
    if (!candidate || !/^[A-Za-z_]/.test(candidate)) candidate = `${prefix}_${candidate || used.size + 1}`;
    const base = candidate;
    let suffix = 2;
    while (used.has(candidate)) candidate = `${base}_${suffix++}`;
    used.add(candidate);
    return candidate;
  };

  const normalizeDraft = (draft) => {
    const used = new Set();
    const idMap = new Map();
    const steps = (Array.isArray(draft.steps) ? draft.steps : []).slice(0, 40).map((raw, index) => {
      const id = validBpmnId(raw.id, "Step", used);
      idMap.set(String(raw.id || ""), id);
      return {
        id,
        type: bpmnTypeFromAi(raw.type),
        name: String(raw.name || "").trim() || `Step ${index + 1}`,
        owner: String(raw.owner || "").trim(),
        system: String(raw.system || "").trim(),
        description: String(raw.description || "").trim(),
        control: String(raw.control || "").trim(),
        sla: String(raw.sla || "").trim(),
        component_id: "",
        code_refs: Array.isArray(raw.code_refs) ? raw.code_refs : [],
        api_endpoints: Array.isArray(raw.api_endpoints) ? raw.api_endpoints : [],
        mcp_tools: Array.isArray(raw.mcp_tools) ? raw.mcp_tools : [],
        links: Array.isArray(raw.links) ? raw.links : [],
      };
    });
    if (!steps.some((step) => step.type === "bpmn:StartEvent")) {
      steps.unshift({ id: validBpmnId("StartEvent_1", "StartEvent", used), type: "bpmn:StartEvent", name: "Start", owner: "", system: "", description: "", control: "", sla: "", component_id: "", code_refs: [], api_endpoints: [], mcp_tools: [], links: [] });
    }
    if (!steps.some((step) => step.type === "bpmn:EndEvent")) {
      steps.push({ id: validBpmnId("EndEvent_1", "EndEvent", used), type: "bpmn:EndEvent", name: "Complete", owner: "", system: "", description: "", control: "", sla: "", component_id: "", code_refs: [], api_endpoints: [], mcp_tools: [], links: [] });
    }
    const stepIds = new Set(steps.map((step) => step.id));
    let connections = (Array.isArray(draft.connections) ? draft.connections : []).map((raw, index) => ({
      id: validBpmnId(raw.id, "Flow", used),
      from: idMap.get(String(raw.from || "")) || String(raw.from || ""),
      to: idMap.get(String(raw.to || "")) || String(raw.to || ""),
      label: String(raw.label || "").trim(),
    })).filter((flow) => stepIds.has(flow.from) && stepIds.has(flow.to) && flow.from !== flow.to);
    if (!connections.length && steps.length > 1) {
      connections = steps.slice(0, -1).map((step, index) => ({ id: validBpmnId(`Flow_${index + 1}`, "Flow", used), from: step.id, to: steps[index + 1].id, label: "" }));
    }
    return { steps, connections };
  };

  const processXmlFromDraft = (draft) => {
    const normalized = normalizeDraft(draft);
    const processId = `Process_${String(draft.temp_id || draft.name || "DevReady").replace(/[^A-Za-z0-9_]+/g, "_")}`;
    const processMeta = {
      clientName: el.clientName.value.trim(),
      owner: draft.owner || "",
      purpose: draft.purpose || "",
      scope: draft.scope || "",
      trigger: draft.trigger || "",
      outcome: draft.outcome || "",
      inputs: draft.inputs || [],
      outputs: draft.outputs || [],
      systems: draft.systems || [],
      controls: draft.controls || [],
      kpis: draft.kpis || [],
    };
    const processDocs = `${METADATA_PREFIX}${JSON.stringify(processMeta)}`;
    const stepXml = normalized.steps.map((step) => {
      const meta = {
        owner: step.owner,
        system: step.system,
        control: step.control,
        sla: step.sla,
        codeRefs: step.code_refs,
        apiEndpoints: step.api_endpoints,
        mcpTools: step.mcp_tools,
        links: step.links,
      };
      const docs = [
        step.description ? `<bpmn:documentation>${escapeXml(step.description)}</bpmn:documentation>` : "",
        `<bpmn:documentation>${escapeXml(`${METADATA_PREFIX}${JSON.stringify(meta)}`)}</bpmn:documentation>`,
      ].join("");
      return `    <bpmn:${xmlTagForType(step.type)} id="${escapeXml(step.id)}" name="${escapeXml(step.name)}">${docs}</bpmn:${xmlTagForType(step.type)}>`;
    }).join("\n");
    const flowXml = normalized.connections.map((flow) => `    <bpmn:sequenceFlow id="${escapeXml(flow.id)}" sourceRef="${escapeXml(flow.from)}" targetRef="${escapeXml(flow.to)}"${flow.label ? ` name="${escapeXml(flow.label)}"` : ""} />`).join("\n");
    const positions = new Map();
    const outgoing = new Map(normalized.steps.map((step) => [step.id, []]));
    const incomingCount = new Map(normalized.steps.map((step) => [step.id, 0]));
    normalized.connections.forEach((flow) => {
      outgoing.get(flow.from)?.push(flow.to);
      incomingCount.set(flow.to, (incomingCount.get(flow.to) || 0) + 1);
    });
    const levelById = new Map();
    const startIds = normalized.steps
      .filter((step) => step.type === "bpmn:StartEvent" || incomingCount.get(step.id) === 0)
      .map((step) => step.id);
    const queue = startIds.length ? [...startIds] : [normalized.steps[0]?.id].filter(Boolean);
    queue.forEach((id) => levelById.set(id, 0));
    for (let index = 0; index < queue.length; index += 1) {
      const source = queue[index];
      const nextLevel = (levelById.get(source) || 0) + 1;
      for (const target of outgoing.get(source) || []) {
        if (levelById.has(target)) continue;
        levelById.set(target, nextLevel);
        queue.push(target);
      }
    }
    let fallbackLevel = Math.max(0, ...levelById.values());
    normalized.steps.forEach((step) => {
      if (!levelById.has(step.id)) levelById.set(step.id, ++fallbackLevel);
    });
    const stepsByLevel = new Map();
    normalized.steps.forEach((step) => {
      const level = levelById.get(step.id) || 0;
      if (!stepsByLevel.has(level)) stepsByLevel.set(level, []);
      stepsByLevel.get(level).push(step);
    });
    for (const [level, levelSteps] of stepsByLevel) {
      levelSteps.forEach((step, row) => {
        const centeredRow = row - ((levelSteps.length - 1) / 2);
        positions.set(step.id, { x: 110 + level * 185, y: 245 + centeredRow * 145 });
      });
    }
    const shapeXml = normalized.steps.map((step) => {
      const pos = positions.get(step.id);
      const event = ["bpmn:StartEvent", "bpmn:EndEvent"].includes(step.type);
      const gateway = step.type.endsWith("Gateway");
      const width = event ? 36 : (gateway ? 50 : 120);
      const height = event ? 36 : (gateway ? 50 : 80);
      pos.width = width;
      pos.height = height;
      return `      <bpmndi:BPMNShape id="${escapeXml(step.id)}_di" bpmnElement="${escapeXml(step.id)}"><dc:Bounds x="${pos.x}" y="${pos.y}" width="${width}" height="${height}" /></bpmndi:BPMNShape>`;
    }).join("\n");
    const edgeXml = normalized.connections.map((flow) => {
      const source = positions.get(flow.from);
      const target = positions.get(flow.to);
      const sx = source.x + source.width;
      const sy = source.y + source.height / 2;
      const tx = target.x;
      const ty = target.y + target.height / 2;
      const middle = Math.round((sx + tx) / 2);
      return `      <bpmndi:BPMNEdge id="${escapeXml(flow.id)}_di" bpmnElement="${escapeXml(flow.id)}"><di:waypoint x="${sx}" y="${sy}" /><di:waypoint x="${middle}" y="${sy}" /><di:waypoint x="${middle}" y="${ty}" /><di:waypoint x="${tx}" y="${ty}" /></bpmndi:BPMNEdge>`;
    }).join("\n");
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions_DevReady" targetNamespace="https://devready.io/process-builder/bpmn">
  <bpmn:process id="${escapeXml(processId)}" name="${escapeXml(draft.name || "Client process")}" isExecutable="false">
    <bpmn:documentation>${escapeXml(processDocs)}</bpmn:documentation>
${stepXml}
${flowXml}
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_DevReady"><bpmndi:BPMNPlane id="BPMNPlane_DevReady" bpmnElement="${escapeXml(processId)}">
${shapeXml}
${edgeXml}
  </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
</bpmn:definitions>`;
    return { xml, elements: normalized.steps, connections: normalized.connections };
  };

  const loadXml = async (xml, process = null) => {
    state.importing = true;
    el.canvasLoading.hidden = false;
    el.canvasLoading.textContent = "Loading process diagram…";
    showCanvasError("");
    try {
      await modeler.importXML(xml);
      state.activeProcess = process;
      state.validation = process?.validation || null;
      state.clientChecks = {};
      modeler.get("canvas").zoom("fit-viewport");
      populateProcessForm();
      resetElementForm();
      updateHeader();
      renderReferenceOverlays();
      renderTrace();
      renderValidation(state.validation);
      renderProcessLibrary();
      setDirty(false);
      el.canvasLoading.hidden = true;
    } catch (error) {
      showCanvasError(`Could not load this BPMN diagram: ${error.message}`);
    } finally {
      state.importing = false;
      updateActions();
    }
  };

  const updateHeader = () => {
    const process = state.activeProcess || {};
    const name = process.name || rootBusinessObject()?.name || el.processName.value || "Untitled client process";
    const status = process.status || "draft";
    el.diagramTitle.textContent = name;
    el.diagramSubtitle.textContent = process.client_name
      ? `${process.client_name} · ${process.element_count || process.elements?.length || modelElements().length} modeled elements · Foundry reconciliation on save`
      : "Use the BPMN palette for manual editing or open AI Intake to describe the flow.";
    el.processStatus.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    el.processStatus.className = `process-badge${status === "validated" ? " validated" : ""}`;
  };

  const modelElements = () => modeler.get("elementRegistry").getAll().filter((item) => item.type && !["label", "bpmn:SequenceFlow", "bpmn:Process", "bpmn:Collaboration", "bpmn:Participant"].includes(item.type));

  const collectProcessPayload = async () => {
    const registry = modeler.get("elementRegistry");
    const elements = modelElements().map((shape) => {
      const { text, metadata } = elementMetadata(shape);
      return {
        id: shape.id,
        type: shape.type,
        name: shape.businessObject?.name || "",
        description: text,
        owner: metadata.owner || "",
        system: metadata.system || "",
        control: metadata.control || "",
        sla: metadata.sla || "",
        component_id: metadata.componentId || "",
        code_refs: metadata.codeRefs || [],
        api_endpoints: metadata.apiEndpoints || [],
        mcp_tools: metadata.mcpTools || [],
        links: metadata.links || [],
      };
    });
    const connections = registry.getAll().filter((item) => item.type === "bpmn:SequenceFlow").map((flow) => ({
      id: flow.id,
      from: flow.source?.id || flow.businessObject?.sourceRef?.id || "",
      to: flow.target?.id || flow.businessObject?.targetRef?.id || "",
      label: flow.businessObject?.name || "",
    }));
    const { xml } = await modeler.saveXML({ format: true });
    return {
      id: state.activeProcess?.id || undefined,
      name: el.processName.value.trim() || rootBusinessObject()?.name || "Untitled client process",
      client_name: el.clientName.value.trim(),
      domain: currentDomain(),
      status: state.activeProcess?.status || "draft",
      owner: el.processOwner.value.trim(),
      purpose: el.processPurpose.value.trim(),
      scope: el.processScope.value.trim(),
      trigger: el.processTrigger.value.trim(),
      outcome: el.processOutcome.value.trim(),
      inputs: lines(el.processInputs.value),
      outputs: lines(el.processOutputs.value),
      systems: lines(el.processSystems.value),
      controls: lines(el.processControls.value),
      kpis: lines(el.processKpis.value),
      bpmn_xml: xml,
      elements,
      connections,
      source: state.activeProcess?.source || "manual",
    };
  };

  const payloadFromDraft = (draft) => {
    const generated = processXmlFromDraft(draft);
    return {
      name: draft.name,
      client_name: el.clientName.value.trim(),
      domain: currentDomain(),
      status: "draft",
      owner: draft.owner,
      purpose: draft.purpose,
      scope: draft.scope,
      trigger: draft.trigger,
      outcome: draft.outcome,
      inputs: draft.inputs || [],
      outputs: draft.outputs || [],
      systems: draft.systems || [],
      controls: draft.controls || [],
      kpis: draft.kpis || [],
      bpmn_xml: generated.xml,
      elements: generated.elements,
      connections: generated.connections,
      source: "ai-intake",
    };
  };

  const loadProcess = async (processId) => {
    if (!confirmDiscard()) return;
    try {
      const data = await fetchJson(`${API}/processes/${encodeURIComponent(processId)}`);
      await loadXml(data.process.bpmn_xml || blankDiagram, data.process);
    } catch (error) {
      toast(`Could not open process: ${error.message}`, "error");
    }
  };

  const saveCurrentProcess = async (statusOverride = "") => {
    try {
      el.saveProcess.disabled = true;
      el.saveProcess.textContent = "Checking Foundry…";
      applyProcessFormToModel();
      const payload = await collectProcessPayload();
      if (statusOverride) payload.status = statusOverride;
      const existing = state.activeProcess?.id;
      const data = await fetchJson(existing ? `${API}/processes/${encodeURIComponent(existing)}` : `${API}/processes`, {
        method: existing ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      state.activeProcess = data.process;
      state.validation = data.process.validation;
      await refreshLibraries();
      populateProcessForm();
      updateHeader();
      renderReferenceOverlays();
      renderTrace();
      renderValidation(state.validation);
      setDirty(false);
      const reused = data.reconciliation?.reused?.length || 0;
      const created = data.reconciliation?.created?.length || 0;
      toast(`Saved. Foundry checked ${data.reconciliation?.checked || 0} activities: ${reused} reused, ${created} new draft component${created === 1 ? "" : "s"}.`);
      return data.process;
    } catch (error) {
      toast(`Save failed: ${error.message}`, "error");
      return null;
    } finally {
      el.saveProcess.disabled = false;
      el.saveProcess.textContent = "Save + reconcile Foundry";
    }
  };

  const renderProcessLibrary = () => {
    const query = el.librarySearch.value.trim().toLowerCase();
    const rows = state.processes.filter((process) => !query || JSON.stringify(process).toLowerCase().includes(query));
    el.processLibrary.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty-library";
      empty.textContent = query ? "No saved process matches this search." : "No client processes saved yet. Start manually or describe the flows in AI Intake.";
      el.processLibrary.appendChild(empty);
      return;
    }
    for (const process of rows) {
      const entry = document.createElement("div");
      entry.className = "library-entry";
      const button = document.createElement("button");
      button.type = "button";
      button.className = `library-item${process.id === state.activeProcess?.id ? " active" : ""}`;
      const title = document.createElement("strong");
      title.textContent = process.name;
      const client = document.createElement("span");
      client.textContent = process.client_name || "Client not named";
      const meta = document.createElement("small");
      meta.className = "library-meta";
      const validation = process.validation || {};
      meta.innerHTML = `<span>${String(process.status || "draft").toUpperCase()}</span><span>${validation.score ?? "--"}/100</span>`;
      button.append(title, client, meta);
      button.addEventListener("click", () => loadProcess(process.id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "library-delete";
      remove.textContent = "×";
      remove.title = `Delete ${process.name}`;
      remove.setAttribute("aria-label", `Delete ${process.name}`);
      remove.addEventListener("click", async () => {
        if (!window.confirm(`Delete the saved process “${process.name}”? Foundry components will be preserved and their usage will be recalculated.`)) return;
        try {
          await fetchJson(`${API}/processes/${encodeURIComponent(process.id)}?confirmed=true`, { method: "DELETE" });
          if (state.activeProcess?.id === process.id) await loadXml(blankDiagram, null);
          await refreshLibraries();
          toast(`${process.name} deleted. Reusable Foundry components were preserved.`);
        } catch (error) {
          toast(`Process deletion failed: ${error.message}`, "error");
        }
      });
      entry.append(button, remove);
      el.processLibrary.appendChild(entry);
    }
  };

  const openComponentDialog = (component = null) => {
    el.componentDialogTitle.textContent = component ? "Edit reusable component" : "Create reusable component";
    el.componentId.value = component?.id || "";
    el.componentName.value = component?.name || "";
    el.componentKind.value = component?.kind || "activity";
    el.componentStatus.value = component?.status || "draft";
    el.componentDescription.value = component?.description || "";
    el.componentAliases.value = lineText(component?.aliases);
    el.componentCodeRefs.value = lineText(component?.code_refs);
    el.componentApiEndpoints.value = lineText(component?.api_endpoints);
    el.componentMcpTools.value = lineText(component?.mcp_tools);
    el.componentLinks.value = lineText(component?.links);
    el.deleteComponent.hidden = !component;
    el.componentDialog.showModal();
  };

  const renderFoundryLibrary = () => {
    const query = el.librarySearch.value.trim().toLowerCase();
    const rows = state.components.filter((component) => !query || JSON.stringify(component).toLowerCase().includes(query));
    el.foundryLibrary.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty-library";
      empty.textContent = "No Foundry component matches this search.";
      el.foundryLibrary.appendChild(empty);
      return;
    }
    for (const component of rows) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "library-item";
      const title = document.createElement("strong");
      title.textContent = component.name;
      const detail = document.createElement("span");
      detail.textContent = `${component.kind} · ${component.implementation_status}`;
      const meta = document.createElement("small");
      meta.className = "library-meta";
      meta.innerHTML = `<span>${String(component.status || "draft").toUpperCase()}</span><span>${component.used_by_processes?.length || 0} process${component.used_by_processes?.length === 1 ? "" : "es"}</span>`;
      button.append(title, detail, meta);
      button.addEventListener("click", () => openComponentDialog(component));
      el.foundryLibrary.appendChild(button);
    }
  };

  const renderLibraries = () => {
    el.processCount.textContent = state.processes.length;
    el.componentCount.textContent = state.components.length;
    renderProcessLibrary();
    renderFoundryLibrary();
    populateComponentSelect(elementMetadata(state.selectedElement).metadata?.componentId || "");
  };

  const refreshLibraries = async () => {
    const [processData, componentData] = await Promise.all([
      fetchJson(`${API}/processes`),
      fetchJson(`${API}/components`),
    ]);
    state.processes = processData.processes || [];
    state.components = componentData.components || [];
    renderLibraries();
  };

  const switchLibraryTab = (tab) => {
    state.libraryTab = tab;
    document.querySelectorAll("[data-library-tab]").forEach((button) => {
      const active = button.dataset.libraryTab === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    el.processLibrary.hidden = tab !== "processes";
    el.foundryLibrary.hidden = tab !== "foundry";
    el.librarySearch.placeholder = tab === "foundry" ? "Search Foundry components" : "Search processes";
    el.librarySearch.value = "";
    renderLibraries();
    if (tab === "foundry") history.replaceState(null, "", `${location.pathname}${location.search}#foundry`);
  };

  const switchToolTab = (tab) => {
    state.toolTab = tab;
    document.querySelectorAll("[data-tool-tab]").forEach((button) => {
      const active = button.dataset.toolTab === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-tool-pane]").forEach((pane) => {
      const active = pane.dataset.toolPane === tab;
      pane.classList.toggle("active", active);
      pane.hidden = !active;
    });
    if (tab === "trace") renderTrace();
  };

  const renderReferenceOverlays = () => {
    const overlays = modeler.get("overlays");
    overlays.remove({ type: "devready-references" });
    for (const shape of modelElements()) {
      const { metadata } = elementMetadata(shape);
      const badges = [];
      if (metadata.componentId) badges.push(["F", "foundry"]);
      if (metadata.codeRefs?.length) badges.push(["</>", "code"]);
      if (metadata.apiEndpoints?.length) badges.push(["API", "api"]);
      if (metadata.mcpTools?.length) badges.push(["MCP", "mcp"]);
      if (!badges.length) continue;
      const stack = document.createElement("div");
      stack.className = "reference-stack";
      for (const [label, kind] of badges) {
        const badge = document.createElement("span");
        badge.className = `reference-badge ${kind}`;
        badge.textContent = label;
        stack.appendChild(badge);
      }
      overlays.add(shape.id, "devready-references", { position: { top: -9, right: 2 }, html: stack });
    }
  };

  const appendRef = (container, value) => {
    const row = document.createElement("span");
    row.className = "trace-ref";
    row.textContent = value;
    row.title = value;
    container.appendChild(row);
  };

  const renderTrace = () => {
    el.traceList.replaceChildren();
    const rows = modelElements().filter((shape) => shape.type !== "bpmn:StartEvent" && shape.type !== "bpmn:EndEvent");
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "Add process activities to build the implementation map.";
      el.traceList.appendChild(empty);
      return;
    }
    const components = new Map(state.components.map((component) => [component.id, component]));
    for (const shape of rows) {
      const { metadata } = elementMetadata(shape);
      const component = components.get(metadata.componentId);
      const codeRefs = metadata.codeRefs?.length ? metadata.codeRefs : (component?.code_refs || []);
      const apiEndpoints = metadata.apiEndpoints?.length ? metadata.apiEndpoints : (component?.api_endpoints || []);
      const mcpTools = metadata.mcpTools?.length ? metadata.mcpTools : (component?.mcp_tools || []);
      const links = metadata.links?.length ? metadata.links : (component?.links || []);
      const card = document.createElement("article");
      card.className = "trace-card";
      const title = document.createElement("strong");
      title.textContent = shape.businessObject?.name || shape.id;
      const subtitle = document.createElement("span");
      subtitle.textContent = component ? `Foundry: ${component.name}` : "Foundry link pending save";
      const pills = document.createElement("div");
      pills.className = "trace-pills";
      const categories = [
        [component ? "Foundry" : "Foundry pending", "foundry", component ? 1 : 0],
        ["Code", "code", codeRefs.length],
        ["API", "api", apiEndpoints.length],
        ["MCP", "mcp", mcpTools.length],
      ];
      for (const [label, kind, count] of categories) {
        const pill = document.createElement("span");
        pill.className = `trace-pill ${kind}`;
        pill.textContent = `${label} ${count}`;
        pills.appendChild(pill);
      }
      const details = document.createElement("div");
      details.className = "trace-details";
      [...codeRefs, ...apiEndpoints, ...mcpTools, ...links].forEach((ref) => appendRef(details, ref));
      if (!details.childElementCount) appendRef(details, "No implementation reference yet");
      card.append(title, subtitle, pills, details);
      card.addEventListener("click", () => {
        modeler.get("selection").select(shape);
        switchToolTab("details");
      });
      el.traceList.appendChild(card);
    }
  };

  const renderValidation = (validation) => {
    state.validation = validation || null;
    el.validationIssues.replaceChildren();
    el.clientChecklist.replaceChildren();
    if (!validation) {
      el.qualityScore.textContent = "--";
      el.validationSummary.className = "validation-summary neutral";
      el.validationSummary.innerHTML = "<strong>Not checked</strong><span>Run validation after the draft is reviewed.</span>";
      el.markValidated.disabled = true;
      return;
    }
    el.qualityScore.textContent = validation.score;
    const kind = validation.errors ? "bad" : (validation.warnings ? "warn" : "good");
    el.validationSummary.className = `validation-summary ${kind}`;
    el.validationSummary.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = validation.ok ? "Structure passes" : "Structural changes required";
    const detail = document.createElement("span");
    detail.textContent = `${validation.errors} error${validation.errors === 1 ? "" : "s"} · ${validation.warnings} warning${validation.warnings === 1 ? "" : "s"}`;
    el.validationSummary.append(title, detail);
    for (const issue of validation.issues || []) {
      const row = document.createElement("div");
      row.className = `validation-issue ${issue.severity}`;
      const icon = document.createElement("span");
      icon.className = "issue-icon";
      icon.textContent = issue.severity === "error" ? "!" : "i";
      const message = document.createElement("span");
      message.textContent = issue.message;
      row.append(icon, message);
      if (issue.element_id && modeler.get("elementRegistry").get(issue.element_id)) {
        const focus = document.createElement("button");
        focus.type = "button";
        focus.className = "text-button";
        focus.textContent = "Show on diagram";
        focus.addEventListener("click", () => {
          const shape = modeler.get("elementRegistry").get(issue.element_id);
          modeler.get("selection").select(shape);
          modeler.get("canvas").scrollToElement(shape);
        });
        row.appendChild(focus);
      }
      el.validationIssues.appendChild(row);
    }
    for (const item of validation.client_validation_checklist || []) {
      const label = document.createElement("label");
      label.className = "check-row";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(state.clientChecks[item.key]);
      input.addEventListener("change", () => {
        state.clientChecks[item.key] = input.checked;
        updateValidatedButton();
      });
      const text = document.createElement("span");
      text.textContent = item.label;
      label.append(input, text);
      el.clientChecklist.appendChild(label);
    }
    updateValidatedButton();
  };

  const updateValidatedButton = () => {
    const checklist = state.validation?.client_validation_checklist || [];
    const complete = checklist.length > 0 && checklist.every((item) => state.clientChecks[item.key]);
    el.markValidated.disabled = !state.validation?.ok || !complete;
  };

  const runValidation = async () => {
    try {
      el.runValidation.disabled = true;
      el.runValidation.textContent = "Checking…";
      const payload = await collectProcessPayload();
      const data = await fetchJson(`${API}/validate`, { method: "POST", body: JSON.stringify(payload) });
      state.clientChecks = {};
      renderValidation(data.validation);
      switchToolTab("validation");
      toast(data.validation.ok ? "Automated validation passed. Complete the client confirmation checklist." : "Validation found structural issues that need attention.", data.validation.ok ? "" : "error");
    } catch (error) {
      toast(`Validation failed: ${error.message}`, "error");
    } finally {
      el.runValidation.disabled = false;
      el.runValidation.textContent = "Run validation";
    }
  };

  const appendChatMessage = (role, message, kind = "") => {
    const article = document.createElement("article");
    article.className = `chat-message ${role}${kind ? ` ${kind}` : ""}`;
    const strong = document.createElement("strong");
    strong.textContent = role === "user" ? "You" : "DevReady Process Agent";
    const paragraph = document.createElement("p");
    paragraph.textContent = message;
    article.append(strong, paragraph);
    el.chatConversation.appendChild(article);
    el.chatConversation.scrollTop = el.chatConversation.scrollHeight;
  };

  const applyDraft = async (draft) => {
    if (!confirmDiscard()) return;
    const generated = processXmlFromDraft(draft);
    const process = payloadFromDraft(draft);
    await loadXml(generated.xml, process);
    state.activeProcess = null;
    populateProcessForm();
    updateHeader();
    setDirty(true);
    switchToolTab("details");
    toast("AI draft opened on the canvas. Review and edit it before saving to AIReady Foundry.");
  };

  const saveAllDrafts = async () => {
    if (!state.drafts.length) return;
    if (!window.confirm(`Save all ${state.drafts.length} staged process drafts and reconcile every activity with AIReady Foundry?`)) return;
    const button = document.getElementById("saveAllDrafts");
    if (button) { button.disabled = true; button.textContent = "Saving…"; }
    let saved = 0;
    try {
      for (const draft of state.drafts) {
        await fetchJson(`${API}/processes`, { method: "POST", body: JSON.stringify(payloadFromDraft(draft)) });
        saved += 1;
      }
      await refreshLibraries();
      toast(`${saved} client process drafts saved and reconciled with AIReady Foundry.`);
      switchLibraryTab("processes");
    } catch (error) {
      toast(`Saved ${saved} drafts before an error: ${error.message}`, "error");
    } finally {
      if (button) { button.disabled = false; button.textContent = `Save all ${state.drafts.length} reviewed drafts`; }
    }
  };

  const renderDrafts = () => {
    el.draftProcesses.replaceChildren();
    el.draftProcesses.hidden = !state.drafts.length;
    if (!state.drafts.length) return;
    const heading = document.createElement("div");
    heading.className = "draft-heading";
    const title = document.createElement("strong");
    title.textContent = `${state.drafts.length} staged process draft${state.drafts.length === 1 ? "" : "s"}`;
    const saveAll = document.createElement("button");
    saveAll.id = "saveAllDrafts";
    saveAll.type = "button";
    saveAll.className = "text-button";
    saveAll.textContent = `Save all ${state.drafts.length} reviewed drafts`;
    saveAll.addEventListener("click", saveAllDrafts);
    heading.append(title, saveAll);
    el.draftProcesses.appendChild(heading);
    state.drafts.forEach((draft, index) => {
      const card = document.createElement("article");
      card.className = "draft-card";
      const name = document.createElement("strong");
      name.textContent = `${index + 1}. ${draft.name}`;
      const detail = document.createElement("span");
      detail.textContent = `${draft.steps?.length || 0} elements · ${draft.owner || "owner to confirm"} · ${draft.purpose || "purpose to confirm"}`;
      const actions = document.createElement("div");
      actions.className = "draft-card-actions";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "pb-button primary";
      open.textContent = "Open for review";
      open.addEventListener("click", () => applyDraft(draft));
      const save = document.createElement("button");
      save.type = "button";
      save.className = "pb-button secondary";
      save.textContent = "Save this draft";
      save.addEventListener("click", async () => {
        try {
          save.disabled = true;
          await fetchJson(`${API}/processes`, { method: "POST", body: JSON.stringify(payloadFromDraft(draft)) });
          await refreshLibraries();
          save.textContent = "Saved";
          toast(`${draft.name} saved and reconciled with AIReady Foundry.`);
        } catch (error) {
          save.disabled = false;
          toast(`Could not save draft: ${error.message}`, "error");
        }
      });
      actions.append(open, save);
      card.append(name, detail, actions);
      el.draftProcesses.appendChild(card);
    });
  };

  const clearChat = () => {
    state.chatHistory = [];
    state.drafts = [];
    el.chatConversation.replaceChildren();
    appendChatMessage("assistant", "Tell me what the client does and describe their major business flows from trigger to outcome. Include people, systems, decisions, exceptions, controls, and any known code, API, MCP, or URL references.");
    renderDrafts();
    el.chatPrompt.value = "";
  };

  const sendChat = async () => {
    const message = el.chatPrompt.value.trim();
    if (!message) return;
    appendChatMessage("user", message);
    state.chatHistory.push({ role: "user", content: message });
    el.chatPrompt.value = "";
    el.sendChat.disabled = true;
    el.sendChat.textContent = "Designing…";
    try {
      const activeProcess = await collectProcessPayload().catch(() => ({}));
      const data = await fetchJson(`${API}/chat`, {
        method: "POST",
        body: JSON.stringify({ message, history: state.chatHistory.slice(0, -1), active_process: activeProcess }),
      });
      const result = data.result || {};
      appendChatMessage("assistant", result.assistant_message || "I prepared a process draft for review.");
      state.chatHistory.push({ role: "assistant", content: result.assistant_message || "" });
      if (result.client_name && !el.clientName.value.trim()) el.clientName.value = result.client_name;
      state.drafts = Array.isArray(result.processes) ? result.processes.slice(0, 5) : [];
      renderDrafts();
    } catch (error) {
      appendChatMessage("assistant", error.message, "error");
      toast(`AI intake failed: ${error.message}`, "error");
    } finally {
      el.sendChat.disabled = false;
      el.sendChat.textContent = "Build process draft";
    }
  };

  const saveComponent = async () => {
    const name = el.componentName.value.trim();
    if (!name) {
      toast("Component name is required.", "error");
      return false;
    }
    const payload = {
      name,
      kind: el.componentKind.value,
      status: el.componentStatus.value,
      description: el.componentDescription.value.trim(),
      aliases: lines(el.componentAliases.value),
      code_refs: lines(el.componentCodeRefs.value),
      api_endpoints: lines(el.componentApiEndpoints.value),
      mcp_tools: lines(el.componentMcpTools.value),
      links: lines(el.componentLinks.value),
    };
    try {
      const componentId = el.componentId.value;
      const data = await fetchJson(componentId ? `${API}/components/${encodeURIComponent(componentId)}` : `${API}/components`, {
        method: componentId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      await refreshLibraries();
      el.componentDialog.close();
      toast(data.reused ? "An existing Foundry component matched this name, so it was reused." : "Foundry component saved.");
      return true;
    } catch (error) {
      toast(`Component save failed: ${error.message}`, "error");
      return false;
    }
  };

  const updateActions = () => {
    try {
      const stack = modeler.get("commandStack");
      el.undo.disabled = !stack.canUndo();
      el.redo.disabled = !stack.canRedo();
    } catch {
      el.undo.disabled = true;
      el.redo.disabled = true;
    }
  };

  document.querySelectorAll("[data-library-tab]").forEach((button) => button.addEventListener("click", () => switchLibraryTab(button.dataset.libraryTab)));
  document.querySelectorAll("[data-tool-tab]").forEach((button) => button.addEventListener("click", () => switchToolTab(button.dataset.toolTab)));
  el.librarySearch.addEventListener("input", renderLibraries);
  el.createComponent.addEventListener("click", () => openComponentDialog());
  el.processForm.addEventListener("submit", (event) => { event.preventDefault(); applyProcessFormToModel(); updateHeader(); toast("Process details applied to the BPMN model. Save when ready."); });
  el.elementForm.addEventListener("submit", (event) => { event.preventDefault(); applyElementForm(); toast("Activity references applied. Save to reconcile the Foundry component."); });
  el.chatForm.addEventListener("submit", (event) => { event.preventDefault(); sendChat(); });
  el.clearChat.addEventListener("click", clearChat);
  el.newProcess.addEventListener("click", () => { if (confirmDiscard()) { state.activeProcess = null; el.clientName.value = ""; loadXml(blankDiagram, null); } });
  el.importProcess.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", async () => {
    const file = el.fileInput.files?.[0];
    if (!file || !confirmDiscard()) return;
    await loadXml(await file.text(), null);
    el.processName.value = file.name.replace(/\.(bpmn|xml)$/i, "");
    el.diagramTitle.textContent = el.processName.value;
    setDirty(true);
    el.fileInput.value = "";
  });
  el.saveProcess.addEventListener("click", () => saveCurrentProcess());
  el.validateProcess.addEventListener("click", runValidation);
  el.runValidation.addEventListener("click", runValidation);
  el.markValidated.addEventListener("click", async () => {
    const saved = await saveCurrentProcess("validated");
    if (saved) toast("Process marked validated with automated checks and the completed client confirmation gate.");
  });
  el.refreshTrace.addEventListener("click", renderTrace);
  el.undo.addEventListener("click", () => modeler.get("commandStack").undo());
  el.redo.addEventListener("click", () => modeler.get("commandStack").redo());
  el.zoomOut.addEventListener("click", () => modeler.get("zoomScroll").stepZoom(-1));
  el.zoomIn.addEventListener("click", () => modeler.get("zoomScroll").stepZoom(1));
  el.fitDiagram.addEventListener("click", () => modeler.get("canvas").zoom("fit-viewport"));
  el.exportBpmn.addEventListener("click", async () => {
    try {
      const { xml } = await modeler.saveXML({ format: true });
      download(xml, "application/xml", safeFilename(el.processName.value, "bpmn"));
    } catch (error) { toast(`BPMN export failed: ${error.message}`, "error"); }
  });
  el.exportSvg.addEventListener("click", async () => {
    try {
      const { svg } = await modeler.saveSVG();
      download(svg, "image/svg+xml", safeFilename(el.processName.value, "svg"));
    } catch (error) { toast(`SVG export failed: ${error.message}`, "error"); }
  });
  el.componentForm.addEventListener("submit", (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    saveComponent();
  });
  el.deleteComponent.addEventListener("click", async () => {
    const componentId = el.componentId.value;
    const componentName = el.componentName.value.trim();
    if (!componentId || !window.confirm(`Delete the Foundry component “${componentName}”? Components used by any process are protected and will not be deleted.`)) return;
    try {
      await fetchJson(`${API}/components/${encodeURIComponent(componentId)}?confirmed=true`, { method: "DELETE" });
      el.componentDialog.close();
      await refreshLibraries();
      toast(`${componentName} deleted from AIReady Foundry.`);
    } catch (error) {
      toast(`Component deletion blocked: ${error.message}`, "error");
    }
  });

  const eventBus = modeler.get("eventBus");
  eventBus.on("selection.changed", (event) => {
    const shape = event.newSelection?.[0];
    if (!shape || shape.type === "bpmn:SequenceFlow" || shape.type === "label") resetElementForm();
    else populateElementForm(shape);
  });
  eventBus.on("commandStack.changed", () => {
    if (!state.importing) setDirty(true);
    updateActions();
    if (!state.importing) {
      window.requestAnimationFrame(() => {
        renderReferenceOverlays();
        renderTrace();
      });
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  const initialize = async () => {
    try {
      const health = await fetchJson(`${API}/health`);
      el.aiStatus.className = `status-chip ${health.ai_ready ? "ready" : "error"}`;
      el.aiStatus.textContent = health.ai_ready ? `AI ready · ${health.model}` : "AI key not configured";
      el.mcpStatus.title = `MCP endpoint: ${health.mcp_endpoint}`;
    } catch (error) {
      el.aiStatus.className = "status-chip error";
      el.aiStatus.textContent = "API unavailable";
      toast(`Process Builder API unavailable: ${error.message}`, "error");
    }
    try { await refreshLibraries(); } catch (error) { toast(`Library load failed: ${error.message}`, "error"); }
    await loadXml(blankDiagram, null);
    if (location.hash === "#foundry") switchLibraryTab("foundry");
    if (location.hash === "#intake") switchToolTab("intake");
  };

  initialize();
})();
