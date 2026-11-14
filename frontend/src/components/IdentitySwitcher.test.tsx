import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { IdentitySwitcher } from "./IdentitySwitcher";

const identities = [
  { id: "u1", email: "a@example.com", display_name: "Alice" },
  { id: "u2", email: "b@example.com", display_name: "Bob" },
];

describe("IdentitySwitcher", () => {
  it("renders every identity as an option and reflects the selection", () => {
    render(
      <IdentitySwitcher
        identities={identities}
        selectedId="u2"
        headerName="X-Demo-User-ID"
        disabled={false}
        onSelect={() => {}}
      />,
    );

    const select = screen.getByLabelText("Select the demo user to act as") as HTMLSelectElement;
    expect(select).toHaveValue("u2");
    expect(screen.getByRole("option", { name: /Alice/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Bob/ })).toBeInTheDocument();
  });

  it("calls onSelect with the chosen user id", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <IdentitySwitcher
        identities={identities}
        selectedId="u1"
        headerName="X-Demo-User-ID"
        disabled={false}
        onSelect={onSelect}
      />,
    );

    await user.selectOptions(screen.getByLabelText("Select the demo user to act as"), "u2");

    expect(onSelect).toHaveBeenCalledWith("u2");
  });

  it("disables the control when there are no identities", () => {
    render(
      <IdentitySwitcher
        identities={[]}
        selectedId={null}
        headerName="X-Demo-User-ID"
        disabled={false}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByLabelText("Select the demo user to act as")).toBeDisabled();
  });
});
