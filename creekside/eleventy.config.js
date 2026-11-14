export default function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy("src/assets");
  eleventyConfig.addPassthroughCopy("src/admin");
  eleventyConfig.addPassthroughCopy({ "src/_redirects": "_redirects" });

  // Events are individual markdown files so the team can add / remove them in the CMS.
  eleventyConfig.addCollection("happenings", (api) =>
    api
      .getFilteredByGlob("src/events/*.md")
      .filter((e) => e.data.published !== false)
      .sort((a, b) => (a.data.order ?? 99) - (b.data.order ?? 99))
  );

  eleventyConfig.addFilter("json", (v) => JSON.stringify(v));
  eleventyConfig.addFilter("featured", (items) => items.filter((i) => i.data.featured));
  eleventyConfig.addFilter("absolute", (path, base) => new URL(path, base).href);
  eleventyConfig.addShortcode("year", () => String(new Date().getFullYear()));

  return {
    dir: { input: "src", output: "_site", includes: "_includes", data: "_data" },
    markdownTemplateEngine: "njk",
    htmlTemplateEngine: "njk",
  };
}
