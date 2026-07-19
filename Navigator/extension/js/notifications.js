export const NotificationService = {
  container: null,
  initContainer() {
    if (this.container) return;
    const appWrap = document.getElementById("app-wrap") || document.body;
    this.container = document.createElement("div");
    this.container.id = "notification-container";
    appWrap.appendChild(this.container);
  },
  show(message, duration = 3000) {
    this.initContainer();
    const toast = document.createElement("div");
    toast.className = "glass-notification";
    toast.textContent = message;
    this.container.appendChild(toast);
    toast.offsetHeight;
    toast.classList.add("show");
    const hideTimeout = setTimeout(() => {
      toast.classList.remove("show");
      toast.addEventListener("transitionend", () => {
        toast.remove();
      });
    }, duration);
    toast.style.cursor = "pointer";
    toast.addEventListener("click", () => {
      clearTimeout(hideTimeout);
      toast.classList.remove("show");
      toast.addEventListener("transitionend", () => {
        toast.remove();
      });
    });
  }
};
