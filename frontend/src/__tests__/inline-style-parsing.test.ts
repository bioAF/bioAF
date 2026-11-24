import styleToJs from "style-to-js";

/**
 * hast-util-to-jsx-runtime turns a hast `style` string into a React style
 * object through style-to-js, which parses with inline-style-parser. That
 * parser is held at a patched release by a scoped override (DEPENDENCIES.md);
 * these cases go through the real parent so the override cannot change what
 * a valid style means.
 */

test("declarations, semicolons and whitespace", () => {
  expect(styleToJs("color: red; background-color : blue ;; margin:0")).toEqual({
    color: "red",
    backgroundColor: "blue",
    margin: "0",
  });
});

test("comments are removed from properties and values", () => {
  expect(styleToJs("/* lead */ color: /* inner */ red /* tail */; font-weight: bold")).toEqual({
    color: "red",
    fontWeight: "bold",
  });
});

test("quoted and unquoted URLs keep their semicolons, colons and parentheses", () => {
  expect(
    styleToJs(
      "background-image: url('a;b.png'); mask: url(\"https://x.test/m.svg#c\"); list-style: url(data:image/png;base64,AAA=)",
    ),
  ).toEqual({
    backgroundImage: "url('a;b.png')",
    mask: 'url("https://x.test/m.svg#c")',
    listStyle: "url(data:image/png;base64,AAA=)",
  });
});

test("vendor prefixes and custom properties", () => {
  expect(styleToJs("-webkit-transition: none; --brand: #123", { reactCompat: true })).toEqual({
    WebkitTransition: "none",
    "--brand": "#123",
  });
});

test("malformed input keeps the declarations before it or fails with a parse error", () => {
  expect(styleToJs("color: red; : blue")).toEqual({ color: "red" });
  expect(() => styleToJs("color: red; x")).toThrow(/property missing ':'/);
  expect(() => styleToJs("/* never closed")).toThrow(/end of comment missing/i);
});
