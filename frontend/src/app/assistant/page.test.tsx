import { render } from "@testing-library/react";
import AssistantPage from "./page";

const replace = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

beforeEach(() => replace.mockReset());

describe("AssistantPage (legacy route)", () => {
  it("redirects home now that the assistant is a global bubble", () => {
    render(<AssistantPage />);
    expect(replace).toHaveBeenCalledWith("/");
  });
});
