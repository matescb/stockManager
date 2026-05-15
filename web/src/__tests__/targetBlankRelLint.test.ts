// @ts-expect-error ESLint v8 is installed for tests, but this repo does not carry @types/eslint.
import { ESLint } from "eslint";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const targetBlankRelRuleId = "jsx-a11y/anchor-rel-noreferrer-noopener";

type LintMessage = {
  ruleId?: string | null;
  message: string;
};

async function targetBlankRelMessages(source: string): Promise<LintMessage[]> {
  const eslint = new ESLint({ cwd: webRoot, useEslintrc: true });
  const [result] = await eslint.lintText(source, {
    filePath: path.join(webRoot, "src/__tests__/targetBlankRel.fixture.tsx"),
  });

  return (result.messages as LintMessage[])
    .filter((message) => message.ruleId === targetBlankRelRuleId);
}

describe("target=_blank rel lint guard", () => {
  it("rejects target=_blank links without rel", async () => {
    await expect(
      targetBlankRelMessages(
        'export const Link = () => <a href="https://example.com" target="_blank">open</a>;',
      ),
    ).resolves.toHaveLength(1);
  });

  it('rejects rel="noreferrer" without noopener', async () => {
    await expect(
      targetBlankRelMessages(
        'export const Link = () => <a href="https://example.com" target="_blank" rel="noreferrer">open</a>;',
      ),
    ).resolves.toHaveLength(1);
  });

  it('allows target=_blank links with rel="noopener noreferrer"', async () => {
    await expect(
      targetBlankRelMessages(
        'export const Link = () => <a href="https://example.com" target="_blank" rel="noopener noreferrer">open</a>;',
      ),
    ).resolves.toHaveLength(0);
  });

  it('allows target=_blank links with rel={"noopener noreferrer"}', async () => {
    await expect(
      targetBlankRelMessages(
        'export const Link = () => <a href="https://example.com" target="_blank" rel={"noopener noreferrer"}>open</a>;',
      ),
    ).resolves.toHaveLength(0);
  });

  it("allows computed rel values", async () => {
    await expect(
      targetBlankRelMessages(
        'const someVar = "noreferrer"; export const Link = () => <a href="https://example.com" target="_blank" rel={someVar}>open</a>;',
      ),
    ).resolves.toHaveLength(0);
  });
});
