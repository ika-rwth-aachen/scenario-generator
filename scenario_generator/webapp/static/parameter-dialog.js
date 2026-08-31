// Parameter declaration editor and persistence.

/** Parse the stored XML fragment into the declaration fields editable by the dialog. */
function parameterRows(raw) {
  const documentNode = new DOMParser().parseFromString(`<ParameterDeclarations>${raw || ""}</ParameterDeclarations>`, "application/xml");
  return [...documentNode.querySelectorAll("ParameterDeclaration")].map((node) => [node.getAttribute("name") || "", node.getAttribute("parameterType") || "double", node.getAttribute("value") || ""]);
}
/** Rebuild the declaration editor, retaining one empty row for immediate input. */
function renderParameterRows(raw = "") {
  const body = $("#parameter-body"); body.replaceChildren();
  const rows = typeof raw === "string" ? parameterRows(raw) : raw;
  (rows.length ? rows : [["", "double", ""]]).forEach((row, index) => {
    const entry = document.createElement("tr");
    const nameCell = entry.insertCell();
    const typeCell = entry.insertCell();
    const valueCell = entry.insertCell();
    const actionCell = entry.insertCell();
    const nameInput = document.createElement("input");
    const typeSelect = document.createElement("select");
    const valueInput = document.createElement("input");
    const removeButton = document.createElement("button");
    ["boolean", "dateTime", "double", "integer", "string", "unsignedInt", "unsignedShort", "int"].forEach((type) => typeSelect.add(new Option(type, type)));
    nameInput.value = String(row[0] || "");
    typeSelect.value = String(row[1] || "double");
    valueInput.value = String(row[2] || "");
    removeButton.type = "button";
    nameInput.setAttribute("aria-label", `Parameter declaration ${index + 1}, name`);
    typeSelect.setAttribute("aria-label", `Parameter declaration ${index + 1}, type`);
    valueInput.setAttribute("aria-label", `Parameter declaration ${index + 1}, value`);
    removeButton.setAttribute("aria-label", `Remove (−) parameter declaration ${index + 1}`);
    removeButton.textContent = "−";
    removeButton.onclick = () => {
      const rowIndex = [...body.rows].indexOf(entry);
      const remaining = [...body.rows].filter((row) => row !== entry)
        .map((row) => [...row.querySelectorAll("input,select")].map((input) => input.value));
      // Rebuild so the remaining rows renumber their labels instead of going stale.
      renderParameterRows(remaining);
      const focusRow = body.rows[Math.min(rowIndex, body.rows.length - 1)];
      (focusRow?.querySelector("input, select, button") || $("#add-parameter")).focus();
    };
    nameCell.append(nameInput);
    typeCell.append(typeSelect);
    valueCell.append(valueInput);
    actionCell.append(removeButton);
    body.append(entry);
  });
}
/** Escape user-entered attribute values before rebuilding the XML fragment. */
function escapeXml(value) { return String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;"); }
// Dialog actions rebuild XML only after complete rows pass client-side validation.
$("#add-parameter").onclick = () => renderParameterRows([...$("#parameter-body").rows].map((row) => [...row.querySelectorAll("input,select")].map((input) => input.value)).concat([["", "double", ""]]));
$("#save-parameters").onclick = async (event) => {
  // Empty rows are ignored; partially filled rows are rejected before export.
  event.preventDefault();
  const actor = selectedActor();
  const rows = [...$("#parameter-body").rows].map((row) => [...row.querySelectorAll("input,select")].map((input) => input.value.trim()));
  const incompleteIndex = rows.findIndex(([name, _type, value]) => Boolean(name) !== Boolean(value));
  if (incompleteIndex !== -1) {
    const row = $("#parameter-body").rows[incompleteIndex];
    const missingField = [...row.querySelectorAll("input,select")].find((input) => !input.value.trim());
    markFieldInvalid(missingField, "Every parameter declaration requires a name, parameter type, and value. Complete or remove the highlighted declaration.");
    return;
  }
  try {
    await api(`/api/actors/${actor.name}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parameter_declarations: rows.filter(([name, _type, value]) => name && value).map(([name, type, value]) => `<ParameterDeclaration name="${escapeXml(name)}" parameterType="${escapeXml(type)}" value="${escapeXml(value)}"/>`).join("") }),
    });
    $("#parameter-dialog").close();
    await refresh(actor.name);
  } catch (error) {
    setStatus(error.message);
  }
};
