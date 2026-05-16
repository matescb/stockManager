// @ts-expect-error ESLint v8 is installed for tests, but this repo does not carry @types/eslint.
import { ESLint } from "eslint";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const targetBlankRelRuleId = "jsx-a11y/anchor-rel-noreferrer-noopener";
const missingRelMessage = 'Links with target="_blank" must use rel="noopener noreferrer".';
const dynamicRelMessage =
  'Links with static target="_blank" must use a static rel value containing "noopener noreferrer".';

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

  it("reports dynamic rel for static target blank anchors", async () => {
    await expect(
      targetBlankRelMessages(
        'let someVar = "noopener noreferrer"; export const Link = () => <a href="https://example.com" target="_blank" rel={someVar}>open</a>;',
      ),
    ).resolves.toEqual([expect.objectContaining({ message: dynamicRelMessage })]);
  });

  it("allows target=_blank links with rel from module and local const string literals", async () => {
    await expect(
      targetBlankRelMessages(`
        const MODULE_SAFE_REL = "noopener noreferrer";

        export const ModuleLink = () => (
          <a href="https://example.com" target="_blank" rel={MODULE_SAFE_REL}>open</a>
        );

        export const LocalLink = () => {
          const localSafeRel = "noreferrer noopener";

          return <a href="https://example.com" target="_blank" rel={localSafeRel}>open</a>;
        };
      `),
    ).resolves.toHaveLength(0);
  });

  it("accepts as const and satisfies safe rel values", async () => {
    await expect(
      targetBlankRelMessages(`
        const asConstRel = "noopener noreferrer" as const;
        const satisfiesRel = "noreferrer noopener" satisfies string;

        export const AsConstLink = () => (
          <a href="https://example.com" target="_blank" rel={asConstRel}>open</a>
        );

        export const SatisfiesLink = () => (
          <a href="https://example.com" target="_blank" rel={satisfiesRel}>open</a>
        );
      `),
    ).resolves.toHaveLength(0);
  });

  it("validates const string literal rel values with the existing token checks", async () => {
    await expect(
      targetBlankRelMessages(
        'const unsafeRel = "noreferrer"; export const Link = () => <a href="https://example.com" target="_blank" rel={unsafeRel}>open</a>;',
      ),
    ).resolves.toEqual([expect.objectContaining({ message: missingRelMessage })]);
  });

  it("reports dynamic rel for const template literal initializers", async () => {
    await expect(
      targetBlankRelMessages(
        'const safeRel = `noopener noreferrer`; export const Link = () => <a href="https://example.com" target="_blank" rel={safeRel}>open</a>;',
      ),
    ).resolves.toEqual([expect.objectContaining({ message: dynamicRelMessage })]);
  });
});
