const TARGET_BLANK_MESSAGE = 'Links with target="_blank" must use rel="noopener noreferrer".';

function getAttribute(node, name) {
  return node.attributes.find(
    (attribute) => attribute.type === "JSXAttribute" && attribute.name.name === name,
  );
}

function getStaticStringValue(attribute) {
  if (!attribute || !attribute.value) {
    return undefined;
  }

  if (attribute.value.type === "Literal") {
    return typeof attribute.value.value === "string" ? attribute.value.value : undefined;
  }

  if (attribute.value.type !== "JSXExpressionContainer") {
    return undefined;
  }

  const expression = attribute.value.expression;

  if (expression.type === "Literal") {
    return typeof expression.value === "string" ? expression.value : undefined;
  }

  if (expression.type === "TemplateLiteral" && expression.expressions.length === 0) {
    return expression.quasis.map((quasi) => quasi.value.cooked).join("");
  }

  return null;
}

function includesRelToken(rel, token) {
  return rel.split(/\s+/u).includes(token);
}

module.exports = {
  meta: {
    type: "problem",
    docs: {
      description: 'Require rel="noopener noreferrer" on anchors with target="_blank".',
    },
    schema: [],
    messages: {
      missingRel: TARGET_BLANK_MESSAGE,
    },
  },
  create(context) {
    return {
      JSXOpeningElement(node) {
        if (node.name.type !== "JSXIdentifier" || node.name.name !== "a") {
          return;
        }

        const target = getStaticStringValue(getAttribute(node, "target"));

        if (target !== "_blank") {
          return;
        }

        const rel = getStaticStringValue(getAttribute(node, "rel"));

        if (rel === null) {
          return;
        }

        if (
          rel === undefined ||
          !includesRelToken(rel, "noopener") ||
          !includesRelToken(rel, "noreferrer")
        ) {
          context.report({ node, messageId: "missingRel" });
        }
      },
    };
  },
};
