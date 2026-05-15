// @ts-expect-error ESLint v8 is installed for tests, but this repo does not carry @types/eslint.
import { ESLint } from "eslint";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const targetBlankRelMessage = 'Links with target="_blank" must use rel="noopener noreferrer".';

type LintMessage = {
  ruleId?: string | null;
  message: string;
};

async function restrictedSyntaxMessages(source: string): Promise<string[]> {
  const eslint = new ESLint({ cwd: webRoot, useEslintrc: true });
  const [result] = await eslint.lintText(source, {
    filePath: path.join(webRoot, "src/__tests__/targetBlankRel.fixture.tsx"),
  });

  return (result.messages as LintMessage[])
    .filter((message) => message.ruleId === "no-restricted-syntax")
    .map((message) => message.message);
}

describe("target=_blank rel lint guard", () => {
  it('rejects rel="noreferrer" without noopener', async () => {
    await expect(
      restrictedSyntaxMessages(
        'export const Link = () => <a href="https://example.com" target="_blank" rel="noreferrer">open</a>;',
      ),
    ).resolves.toContain(targetBlankRelMessage);
  });

  it("rejects target=_blank links without rel", async () => {
    await expect(
      restrictedSyntaxMessages(
        'export const Link = () => <a href="https://example.com" target="_blank">open</a>;',
      ),
    ).resolves.toContain(targetBlankRelMessage);
  });

  it('allows target=_blank links with rel="noopener noreferrer"', async () => {
    await expect(
      restrictedSyntaxMessages(
        'export const Link = () => <a href="https://example.com" target="_blank" rel="noopener noreferrer">open</a>;',
      ),
    ).resolves.not.toContain(targetBlankRelMessage);
  });
});
