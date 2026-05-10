// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BomProviderAmbiguityModal from "../BomProviderAmbiguityModal";

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BomProviderAmbiguityModal", () => {
  it("submits the selected manufacturer for each pending choice", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();

    render(
      <BomProviderAmbiguityModal
        open
        busy={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
        choices={[{
          entry_id: "entry-1",
          mpn: "AMB-1",
          candidates: [
            { manufacturer: "Alpha", mpn: "AMB-1", description: "Alpha part", source_url: null, image_url: null },
            { manufacturer: "Beta", mpn: "AMB-1", description: "Beta part", source_url: null, image_url: null },
          ],
        }]}
      />,
    );

    await user.click(screen.getByLabelText("Beta"));
    await user.click(screen.getByRole("button", { name: "Import selected" }));

    expect(onConfirm).toHaveBeenCalledWith({ "entry-1": "Beta" });
  });
});
