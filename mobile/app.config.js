// updates.url is https://u.expo.dev/<project id>. The id does not exist until
// `eas init` writes extra.eas.projectId. Until then the URL is omitted.

module.exports = ({ config }) => {
  const projectId = config.extra && config.extra.eas && config.extra.eas.projectId;
  const updates = { ...config.updates };
  if (typeof projectId === "string" && projectId.length > 0) {
    updates.url = `https://u.expo.dev/${projectId}`;
  }
  return {
    ...config,
    updates,
  };
};
