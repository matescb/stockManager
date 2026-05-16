/**
 * In-tree implementation of jsx-a11y/anchor-rel-noreferrer-noopener.
 *
 * eslint-plugin-jsx-a11y does not ship a rule with this name, so this repo
 * carries the rule locally and web/.eslintrc.cjs grafts it into the imported
 * plugin's rules map before enabling it as jsx-a11y/anchor-rel-noreferrer-noopener.
 *
 * Behavior and limitations:
 * - Checks JSX <a> elements only when target is statically known to be "_blank".
 * - Requires a static rel value containing both whitespace-delimited noopener and
 *   noreferrer tokens.
 * - After AUD-100 / #771, a static target="_blank" with dynamic rel reports a
 *   dedicated diagnostic because the rule cannot prove the required tokens exist.
 * - AUD-108 / #788 folds same-file const string literal rel identifiers only;
 *   imports, re-exports, template literals, and computed initializers stay dynamic.
 * - Dynamic target values and custom link components are ignored.
 */
const TARGET_BLANK_MESSAGE = 'Links with target="_blank" must use rel="noopener noreferrer".';
const DYNAMIC_REL_MESSAGE =
  'Links with static target="_blank" must use a static rel value containing "noopener noreferrer".';

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

function findVariable(scope, name) {
  let currentScope = scope;

  while (currentScope) {
    const variable = currentScope.set?.get(name);

    if (variable) {
      return variable;
    }

    currentScope = currentScope.upper;
  }

  return undefined;
}

function unwrapTSConstExpression(expression) {
  let currentExpression = expression;

  while (
    currentExpression?.type === "TSAsExpression" ||
    currentExpression?.type === "TSSatisfiesExpression"
  ) {
    currentExpression = currentExpression.expression;
  }

  return currentExpression;
}

function getConstStringLiteralValue(expression, scope) {
  if (expression.type !== "Identifier") {
    return null;
  }

  const variable = findVariable(scope, expression.name);
  const definition = variable?.defs?.[0];

  if (!definition || definition.type !== "Variable") {
    return null;
  }

  const declaration = definition.parent ?? definition.node.parent;

  if (declaration?.kind !== "const") {
    return null;
  }

  const init = unwrapTSConstExpression(definition.node.init);

  if (!init || init.type !== "Literal" || typeof init.value !== "string") {
    return null;
  }

  return init.value;
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
      dynamicRel: DYNAMIC_REL_MESSAGE,
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

        const relAttribute = getAttribute(node, "rel");
        const rel = getStaticStringValue(relAttribute);
        const resolvedRel =
          rel === null && relAttribute?.value?.type === "JSXExpressionContainer"
            ? getConstStringLiteralValue(relAttribute.value.expression, context.getScope())
            : rel;

        if (resolvedRel === null) {
          // The const lookup could not prove the dynamic rel tokens.
          context.report({ node, messageId: "dynamicRel" });
          return;
        }

        if (
          resolvedRel === undefined ||
          !includesRelToken(resolvedRel, "noopener") ||
          !includesRelToken(resolvedRel, "noreferrer")
        ) {
          context.report({ node, messageId: "missingRel" });
        }
      },
    };
  },
};
