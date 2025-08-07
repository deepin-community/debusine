/**
 * Generate a self-closed notification using Bootstrap toasts
 *
 * :param message: The message to show: it can be a string or an HTML element.
 * :param close_button: Whether to show a close button
 * :param delay: Delay in milliseconds after which the notification disappears.
 */
export function toast_notify({message, close_button=true, delay=2000})
{
    let main_toast_container = document.getElementById("user-message-container").children[0];

    let toast = document.createElement("div");
    toast.classList.add("toast", "align-items-center");
    toast.attributes["role"] = "alert";
    toast.attributes["aria-live"] = "assertive";
    toast.attributes["aria-atomic"] = true;

    let toast_body = document.createElement("div");
    toast_body.classList.add("toast-body");

    if (typeof(message) == "string")
        toast_body.innerText = message;
    else
        toast_body.append(message);

    if (close_button)
    {
        let dflex = document.createElement("div");
        dflex.classList.add("d-flex");
        toast.append(dflex);
        dflex.append(toast_body);

        let close_button = document.createElement("button");
        close_button.classList.add("btn-close", "me-2", "m-auto");
        close_button.attributes["type"] = "button";
        close_button.attributes["data-bs-dismiss"] = "toast";
        close_button.attributes["aria-label"] = "Close";
        dflex.append(close_button);
    } else {
        toast.append(toast_body);
    }

    main_toast_container.append(toast);

    toast.addEventListener("hidden.bs.toast", () => {
        toast.remove();
    });

    let bs_toast = new bootstrap.Toast(toast, {
        autohide: true,
        delay: delay,
    });
    bs_toast.show();
}
