(async () => {
  const { pathToFileURL } = await import("node:url");
  if (!process.env.NAPCAT_MAIN_PATH) throw new Error("NAPCAT_MAIN_PATH is required");
  await import(pathToFileURL(process.env.NAPCAT_MAIN_PATH).href);
})();
