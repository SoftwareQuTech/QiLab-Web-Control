/* Schema-driven config editor for qilab_web_control.
 *
 * Reads qilab's JSON Schema and the current config document (injected as
 * <script type="application/json"> blobs), renders a structured form, and
 * submits the edited document as JSON to /config/save.
 *
 * The renderer *walks* the schema instead of naming fields: objects, arrays,
 * $ref, oneOf discriminated unions, enums and scalars each have one generic
 * case, and serialization is the inverse walk of the same tree. A schema change
 * -- new field, new noise model, new top-level section, a renamed $def -- needs
 * no edit here. Nothing below may reference a qilab field name; the moment it
 * does, the editor starts drifting from the schema again (which is exactly how
 * the previous hand-written renderer silently kept showing an old format).
 *
 * Scalars live in the state tree as strings, the way the inputs produce them,
 * and are coerced to numbers/booleans only in serialize().
 */
(function () {
  "use strict";

  var schemaEl = document.getElementById("qilab-schema");
  var docEl = document.getElementById("qilab-document");
  var defaultDocEl = document.getElementById("qilab-default-document");
  var body = document.getElementById("config-body");
  var form = document.getElementById("config-form");
  var statusEl = document.getElementById("config-save-status");
  var loadDefaultBtn = document.getElementById("config-load-default");
  if (!schemaEl || !body || !form) return;

  var schema, sourceDoc, defaultDoc;
  try {
    schema = JSON.parse(schemaEl.textContent || "null");
    sourceDoc = JSON.parse((docEl && docEl.textContent) || "null") || {};
    defaultDoc = JSON.parse((defaultDocEl && defaultDocEl.textContent) || "null") || {};
  } catch (err) {
    body.textContent = "Failed to parse schema/document: " + err;
    return;
  }
  if (!schema) {
    body.textContent = "Schema unavailable.";
    return;
  }

  // ---- schema helpers ----

  /* Resolve a (possibly $ref) subschema. Keywords written next to the $ref win
     over the definition's own, so a property can retitle what it points at. */
  function resolve(node, depth) {
    if (!node || !node.$ref) return node || {};
    if ((depth || 0) > 32) return {}; // self-referential schema; refuse to spin
    var target = schema;
    node.$ref.replace(/^#\/?/, "").split("/").forEach(function (part) {
      target = (target || {})[part];
    });
    var merged = {};
    Object.keys(target || {}).forEach(function (key) { merged[key] = target[key]; });
    Object.keys(node).forEach(function (key) {
      if (key !== "$ref") merged[key] = node[key];
    });
    return merged.$ref ? resolve(merged, (depth || 0) + 1) : merged;
  }

  function kindOf(node) {
    if (node.oneOf) return "union";
    if (node.enum) return "enum";
    if (node.type === "array") return "array";
    if (node.type === "object" || node.properties) return "object";
    if (node.type === "boolean") return "boolean";
    if (node.type === "integer" || node.type === "number") return "number";
    return "string";
  }

  function isComposite(kind) {
    return kind === "object" || kind === "union" || kind === "array";
  }

  /* The discriminator of a oneOf branch: the const pinned on its "type". */
  function branchType(branch) {
    return (((branch || {}).properties || {}).type || {}).const;
  }

  function branchOf(node, type) {
    var list = node.oneOf || [];
    for (var i = 0; i < list.length; i++) {
      if (branchType(list[i]) === type) return list[i];
    }
    return list[0] || { properties: {} };
  }

  function isRequired(node, key) {
    return (node.required || []).indexOf(key) >= 0;
  }

  function labelOf(node, key) {
    var text = node.title || key;
    return node.unit ? text + " (" + node.unit + ")" : text;
  }

  // ---- blank values (required keys only: optional ones stay unset) ----

  function blankObject(node, type) {
    var value = {};
    var props = node.properties || {};
    if (type) value.type = type;
    (node.required || []).forEach(function (key) {
      if (key !== "type" && props[key]) value[key] = blank(props[key]);
    });
    return value;
  }

  function blank(node) {
    node = resolve(node);
    switch (kindOf(node)) {
      case "object":
        return blankObject(node);
      case "union":
        var branch = (node.oneOf || [])[0] || {};
        return blankObject(branch, branchType(branch));
      case "array":
        // One item even when minItems is 0 -- an empty list has nothing to edit.
        var items = [];
        for (var i = 0; i < Math.max(node.minItems || 0, 1); i++) {
          items.push(blank(node.items || {}));
        }
        return items;
      case "enum":
        return String(node.default != null ? node.default : node.enum[0]);
      case "boolean":
        return ""; // unset, so the schema default applies
      case "number":
        return node.default != null ? String(node.default) : "";
      default:
        return node.default != null ? String(node.default) : "";
    }
  }

  // ---- adopt a loaded document into the state tree ----

  /* Keys the schema does not know cannot be edited or saved (every object is
     additionalProperties:false), so they are dropped -- but reported, because
     silently discarding part of someone's config on save is not acceptable. */
  var dropped = [];

  function childPath(path, key) {
    return path ? path + "." + key : key;
  }

  function adoptObject(node, value, path, type) {
    var props = node.properties || {};
    var out = {};
    if (type) out.type = type;
    Object.keys(value).forEach(function (key) {
      if (key === "type" && type) return;
      if (!props[key]) {
        dropped.push(childPath(path, key));
        return;
      }
      out[key] = adopt(props[key], value[key], childPath(path, key));
    });
    // A required key the document lacks still needs an editable blank.
    (node.required || []).forEach(function (key) {
      if (key !== "type" && props[key] && out[key] === undefined) {
        out[key] = blank(props[key]);
      }
    });
    return out;
  }

  function adopt(node, value, path) {
    node = resolve(node);
    var kind = kindOf(node);

    if (kind === "object" || kind === "union") {
      if (!value || typeof value !== "object" || Array.isArray(value)) return blank(node);
      if (kind === "union") {
        var branch = branchOf(node, value.type);
        return adoptObject(branch, value, path, branchType(branch));
      }
      return adoptObject(node, value, path);
    }
    if (kind === "array") {
      if (!Array.isArray(value) || !value.length) return blank(node);
      return value.map(function (item, index) {
        return adopt(node.items || {}, item, path + "[" + index + "]");
      });
    }
    if (kind === "enum") {
      return node.enum.map(String).indexOf(String(value)) >= 0 ? String(value) : blank(node);
    }
    if (kind === "boolean") {
      return typeof value === "boolean" ? String(value) : blank(node);
    }
    if (value === null || value === undefined || typeof value === "object") return blank(node);
    return String(value);
  }

  function load(doc) {
    dropped = [];
    return adopt(schema, doc, "");
  }

  var state = load(sourceDoc);

  // ---- tiny DOM helper ----
  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (key) {
      var value = attrs[key];
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.slice(0, 2) === "on") node[key] = value;
      else if (value !== undefined && value !== null && value !== false) {
        node.setAttribute(key, value);
      }
    });
    (children || []).forEach(function (child) {
      if (child == null) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function hint(node) {
    return node.description ? h("span", { class: "field-hint", text: node.description }) : null;
  }

  // ---- scalar inputs ----

  function numberInput(node, holder, key, required) {
    var integer = node.type === "integer";
    var attrs = {
      type: "number",
      class: "field-input",
      step: integer ? "1" : "any",
      value: holder[key] != null ? holder[key] : "",
      required: required,
      oninput: function (event) { holder[key] = event.target.value; },
    };
    if (node.minimum !== undefined) attrs.min = node.minimum;
    else if (node.exclusiveMinimum !== undefined) {
      attrs.min = integer ? node.exclusiveMinimum + 1 : node.exclusiveMinimum;
    }
    if (node.maximum !== undefined) attrs.max = node.maximum;
    else if (node.exclusiveMaximum !== undefined) {
      attrs.max = integer ? node.exclusiveMaximum - 1 : node.exclusiveMaximum;
    }
    return h("input", attrs);
  }

  function textInput(node, holder, key, required) {
    return h("input", {
      type: "text",
      class: "field-input",
      value: holder[key] != null ? holder[key] : "",
      required: required,
      oninput: function (event) { holder[key] = event.target.value; },
    });
  }

  function options(values, selected, placeholder) {
    var list = placeholder == null ? [] : [h("option", { value: "", selected: selected === "" }, [placeholder])];
    return list.concat(
      values.map(function (value) {
        return h("option", { value: value, selected: String(value) === String(selected) }, [String(value)]);
      })
    );
  }

  function selectInput(values, holder, key, required, placeholder, rerender) {
    return h(
      "select",
      {
        class: "field-input",
        required: required,
        onchange: function (event) {
          holder[key] = event.target.value;
          if (rerender) render();
        },
      },
      options(values, holder[key], placeholder)
    );
  }

  /* An optional boolean has three states, not two: true, false, and unset (use
     the schema default). A checkbox cannot express the third, so it is a select. */
  function boolInput(node, holder, key, required) {
    var placeholder = required
      ? null
      : "default" + (node.default !== undefined ? " (" + node.default + ")" : "");
    return selectInput(["true", "false"], holder, key, required, placeholder);
  }

  function scalarField(node, holder, key, required) {
    var kind = kindOf(node);
    var input;
    if (kind === "enum") input = selectInput(node.enum, holder, key, required, required ? null : "unset");
    else if (kind === "boolean") input = boolInput(node, holder, key, required);
    else if (kind === "number") input = numberInput(node, holder, key, required);
    else input = textInput(node, holder, key, required);

    return h("label", { class: "field", title: key }, [
      h("span", { text: labelOf(node, key) + (required ? "" : " (optional)") }),
      input,
      hint(node),
    ]);
  }

  // ---- composite renderers ----

  function sectionClass(depth) {
    return depth <= 1 ? "config-section" : "config-subsection";
  }

  /* Render an object's properties into `parent`: consecutive scalars share one
     responsive grid, composites become their own (sub)section. */
  function renderProperties(node, value, depth, parent) {
    var props = node.properties || {};
    var grid = null;
    Object.keys(props).forEach(function (key) {
      if (key === "type") return; // the union discriminator; rendered by renderUnion
      var child = resolve(props[key]);
      var required = isRequired(node, key);
      if (isComposite(kindOf(child))) {
        grid = null;
        parent.appendChild(renderComposite(child, value, key, depth + 1, required));
      } else {
        if (!grid) {
          grid = h("div", { class: "model-fields" });
          parent.appendChild(grid);
        }
        grid.appendChild(scalarField(child, value, key, required));
      }
    });
    return parent;
  }

  function renderObject(node, value, depth) {
    return renderProperties(node, value, depth, document.createDocumentFragment());
  }

  function renderUnion(node, holder, key, depth) {
    var value = holder[key];
    var branch = branchOf(node, value.type);
    var types = (node.oneOf || []).map(branchType);
    var wrap = document.createDocumentFragment();
    wrap.appendChild(
      h("label", { class: "field", title: key + ".type" }, [
        h("span", { text: "type" }),
        h(
          "select",
          {
            class: "field-input",
            onchange: function (event) { switchType(node, value, event.target.value); },
          },
          options(types, value.type)
        ),
      ])
    );
    renderProperties(branch, value, depth, wrap);
    return wrap;
  }

  /* Switch a union to another branch, carrying over any same-named fields so
     retyping a model does not lose values the new branch also has. */
  function switchType(node, value, type) {
    var branch = branchOf(node, type);
    var props = branch.properties || {};
    var carried = {};
    Object.keys(props).forEach(function (key) {
      if (key !== "type" && value[key] !== undefined) carried[key] = value[key];
    });
    Object.keys(value).forEach(function (key) { delete value[key]; });
    var next = blankObject(branch, type);
    Object.keys(next).forEach(function (key) { value[key] = next[key]; });
    Object.keys(carried).forEach(function (key) { value[key] = carried[key]; });
    render();
  }

  function renderArray(node, items, depth) {
    var itemNode = resolve(node.items || {});
    var floor = Math.max(node.minItems || 0, 1);
    var wrap = document.createDocumentFragment();

    items.forEach(function (item, index) {
      var block = h("div", { class: "array-block" }, [
        h("div", { class: "status-row" }, [
          h("span", { class: "label", text: labelOf(itemNode, "item") + " " + (index + 1) }),
          h(
            "button",
            {
              type: "button",
              class: "btn btn-remove",
              disabled: items.length <= floor,
              onclick: function () { items.splice(index, 1); render(); },
            },
            ["Remove"]
          ),
        ]),
      ]);
      block.appendChild(renderNode(itemNode, items, index, depth + 1));
      wrap.appendChild(block);
    });

    wrap.appendChild(
      h(
        "button",
        {
          type: "button",
          class: "btn btn-add",
          onclick: function () { items.push(blank(itemNode)); render(); },
        },
        ["+ Add " + labelOf(itemNode, "item").toLowerCase()]
      )
    );
    return wrap;
  }

  /* Dispatch on kind for a value held at holder[key]. */
  function renderNode(node, holder, key, depth) {
    switch (kindOf(node)) {
      case "object":
        return renderObject(node, holder[key], depth);
      case "union":
        return renderUnion(node, holder, key, depth);
      case "array":
        return renderArray(node, holder[key], depth);
      default:
        return scalarField(node, holder, key, true);
    }
  }

  /* A composite property: its own section, plus a presence toggle when it is
     optional (a partly-filled nested object cannot be expressed as "unset"). */
  function renderComposite(node, value, key, depth, required) {
    var label = labelOf(node, key);
    // title=key so hovering any section still tells the operator which YAML key
    // it writes, however far the schema's titles drift from the field names.
    var section = h("div", { class: sectionClass(depth), title: key }, [
      h("div", { class: "label", text: label }),
      hint(node),
    ]);

    if (!required) {
      var present = value[key] !== undefined;
      section.appendChild(
        h("label", { class: "field-inline", title: key }, [
          h("input", {
            type: "checkbox",
            checked: present,
            onchange: function (event) {
              if (event.target.checked) value[key] = blank(node);
              else delete value[key];
              render();
            },
          }),
          h("span", { text: "Set " + label }),
        ])
      );
      if (!present) return section;
    }

    if (value[key] === undefined) value[key] = blank(node);
    section.appendChild(renderNode(node, value, key, depth));
    return section;
  }

  function render() {
    body.innerHTML = "";
    if (dropped.length) {
      body.appendChild(
        h("div", {
          class: "notice",
          text:
            "These keys are not in the current schema and will be dropped if you save: " +
            dropped.join(", "),
        })
      );
    }
    body.appendChild(renderObject(schema, state, 0));
  }

  // ---- serialize state -> JSON document ----

  function toNumber(raw, integer) {
    if (raw === undefined || raw === null || String(raw).trim() === "") return undefined;
    var value = integer ? parseInt(raw, 10) : Number(raw);
    return Number.isFinite(value) ? value : raw; // keep raw string so the server flags it
  }

  function isEmptyContainer(value) {
    if (Array.isArray(value)) return value.length === 0;
    return value !== null && typeof value === "object" && Object.keys(value).length === 0;
  }

  function serializeObject(node, value, type) {
    var out = {};
    var props = node.properties || {};
    if (type) out.type = type;
    Object.keys(props).forEach(function (key) {
      if (key === "type" || !value || value[key] === undefined) return;
      var child = serialize(props[key], value[key]);
      // Blank scalars and toggled-on-but-untouched sections mean "unset", and an
      // omitted key is what lets qilab apply its own default.
      if (child !== undefined && !isEmptyContainer(child)) out[key] = child;
    });
    return out;
  }

  function serialize(node, value) {
    node = resolve(node);
    switch (kindOf(node)) {
      case "object":
        return serializeObject(node, value);
      case "union":
        var branch = branchOf(node, value && value.type);
        return serializeObject(branch, value, branchType(branch));
      case "array":
        return (value || []).map(function (item) { return serialize(node.items || {}, item); });
      case "boolean":
        return value === "" || value == null ? undefined : value === "true";
      case "number":
        return toNumber(value, node.type === "integer");
      default:
        return value === "" || value == null ? undefined : value;
    }
  }

  if (loadDefaultBtn) {
    loadDefaultBtn.addEventListener("click", function () {
      if (!window.confirm("Replace the form with a blank default config? This does not save until you click Save.")) {
        return;
      }
      state = load(defaultDoc);
      if (statusEl) statusEl.textContent = "";
      render();
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!form.reportValidity()) return;
    if (statusEl) statusEl.textContent = "Saving…";
    fetch("/config/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(serialize(schema, state)),
    })
      .then(function () { window.location.href = "/?tab=config"; })
      .catch(function (err) {
        if (statusEl) statusEl.textContent = "Save request failed: " + err;
      });
  });

  render();
})();
