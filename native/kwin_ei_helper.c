#include <errno.h>
#include <gio/gio.h>
#include <gio/gunixfdlist.h>
#include <libei.h>
#include <linux/input-event-codes.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct devices {
    struct ei_device *absolute;
    struct ei_device *keyboard;
    bool absolute_ready;
    bool keyboard_ready;
    uint32_t sequence;
};

static void release_devices(struct devices *devices)
{
    devices->absolute = ei_device_unref(devices->absolute);
    devices->keyboard = ei_device_unref(devices->keyboard);
}

static int connect_to_kwin(GDBusConnection **connection_out, int *cookie_out)
{
    GError *error = NULL;
    GUnixFDList *fd_list = NULL;
    GDBusConnection *connection = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &error);
    if (!connection) {
        fprintf(stderr, "kwin-ei: session bus connection failed: %s\n", error->message);
        g_error_free(error);
        return -1;
    }

    GVariant *reply = g_dbus_connection_call_with_unix_fd_list_sync(
        connection,
        "org.kde.KWin",
        "/org/kde/KWin/EIS/RemoteDesktop",
        "org.kde.KWin.EIS.RemoteDesktop",
        "connectToEIS",
        g_variant_new("(i)", 3),
        G_VARIANT_TYPE("(hi)"),
        G_DBUS_CALL_FLAGS_NONE,
        5000,
        NULL,
        &fd_list,
        NULL,
        &error);
    if (!reply) {
        fprintf(stderr, "kwin-ei: connectToEIS failed: %s\n", error->message);
        g_error_free(error);
        g_object_unref(connection);
        return -1;
    }

    gint32 handle = -1;
    gint32 cookie = -1;
    g_variant_get(reply, "(hi)", &handle, &cookie);
    int fd = g_unix_fd_list_get(fd_list, handle, &error);
    g_variant_unref(reply);
    g_object_unref(fd_list);
    if (fd < 0) {
        fprintf(stderr, "kwin-ei: failed to receive EIS descriptor: %s\n", error->message);
        g_error_free(error);
        g_object_unref(connection);
        return -1;
    }

    *connection_out = connection;
    *cookie_out = cookie;
    return fd;
}

static void handle_event(struct ei_event *event, struct devices *devices)
{
    struct ei_device *device = ei_event_get_device(event);
    switch (ei_event_get_type(event)) {
    case EI_EVENT_SEAT_ADDED:
        ei_seat_bind_capabilities(
            ei_event_get_seat(event),
            EI_DEVICE_CAP_POINTER_ABSOLUTE,
            EI_DEVICE_CAP_BUTTON,
            EI_DEVICE_CAP_KEYBOARD,
            NULL);
        break;
    case EI_EVENT_DEVICE_ADDED:
        if (!devices->absolute &&
            ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE)) {
            devices->absolute = ei_device_ref(device);
        }
        if (!devices->keyboard &&
            ei_device_has_capability(device, EI_DEVICE_CAP_KEYBOARD)) {
            devices->keyboard = ei_device_ref(device);
        }
        break;
    case EI_EVENT_DEVICE_RESUMED:
        if (device == devices->absolute) {
            ei_device_start_emulating(device, ++devices->sequence);
            devices->absolute_ready = true;
        }
        if (device == devices->keyboard) {
            ei_device_start_emulating(device, ++devices->sequence);
            devices->keyboard_ready = true;
        }
        break;
    case EI_EVENT_DEVICE_PAUSED:
    case EI_EVENT_DEVICE_REMOVED:
        if (device == devices->absolute) {
            devices->absolute_ready = false;
        }
        if (device == devices->keyboard) {
            devices->keyboard_ready = false;
        }
        break;
    default:
        break;
    }
}

static int wait_for_devices(struct ei *ei, struct devices *devices)
{
    struct pollfd pollfd = {
        .fd = ei_get_fd(ei),
        .events = POLLIN,
    };

    for (int attempts = 0; attempts < 30; attempts++) {
        int result = poll(&pollfd, 1, 100);
        if (result < 0 && errno != EINTR) {
            return -1;
        }
        ei_dispatch(ei);
        struct ei_event *event;
        while ((event = ei_get_event(ei))) {
            handle_event(event, devices);
            ei_event_unref(event);
        }
        if (devices->absolute_ready && devices->keyboard_ready) {
            return 0;
        }
    }
    return -1;
}

static void frame(struct ei_device *device, uint64_t time)
{
    ei_device_frame(device, time);
}

static unsigned int button_code(unsigned int button)
{
    static const unsigned int buttons[] = {BTN_LEFT, BTN_RIGHT, BTN_MIDDLE};
    return button < 3 ? buttons[button] : 0;
}

static int execute_command(char *line, struct ei *ei, struct devices *devices)
{
    double x = 0;
    double y = 0;
    unsigned int value = 0;
    unsigned int state = 0;
    uint64_t now = ei_now(ei);

    if (sscanf(line, "move %lf %lf", &x, &y) == 2) {
        ei_device_pointer_motion_absolute(devices->absolute, x, y);
        frame(devices->absolute, now);
        return 0;
    }
    if (sscanf(line, "click %lf %lf %u", &x, &y, &value) == 3) {
        unsigned int code = button_code(value);
        if (!code) {
            return -1;
        }
        ei_device_pointer_motion_absolute(devices->absolute, x, y);
        frame(devices->absolute, now);
        ei_device_button_button(devices->absolute, code, true);
        frame(devices->absolute, now + 1000);
        ei_device_button_button(devices->absolute, code, false);
        frame(devices->absolute, now + 2000);
        return 0;
    }
    if (sscanf(line, "button %u %u", &value, &state) == 2) {
        unsigned int code = button_code(value);
        if (!code || state > 1) {
            return -1;
        }
        ei_device_button_button(devices->absolute, code, state != 0);
        frame(devices->absolute, now);
        return 0;
    }
    if (sscanf(line, "key %u %u", &value, &state) == 2) {
        if (state > 1) {
            return -1;
        }
        ei_device_keyboard_key(devices->keyboard, value, state != 0);
        frame(devices->keyboard, now);
        return 0;
    }
    if (strcmp(line, "quit\n") == 0) {
        return 1;
    }
    return -1;
}

int main(void)
{
    GDBusConnection *connection = NULL;
    int cookie = -1;
    int fd = connect_to_kwin(&connection, &cookie);
    if (fd < 0) {
        return EXIT_FAILURE;
    }

    struct ei *ei = ei_new_sender(NULL);
    if (!ei) {
        g_object_unref(connection);
        return EXIT_FAILURE;
    }
    ei_configure_name(ei, "computer-control-mcp");
    if (ei_setup_backend_fd(ei, fd) != 0) {
        ei_unref(ei);
        g_object_unref(connection);
        return EXIT_FAILURE;
    }

    struct devices devices = {0};
    if (wait_for_devices(ei, &devices) != 0) {
        fprintf(stderr, "kwin-ei: timed out waiting for input devices\n");
        release_devices(&devices);
        ei_unref(ei);
        g_object_unref(connection);
        return EXIT_FAILURE;
    }

    puts("ready");
    fflush(stdout);

    char line[256];
    while (fgets(line, sizeof(line), stdin)) {
        int result = execute_command(line, ei, &devices);
        if (result == 1) {
            break;
        }
        puts(result == 0 ? "ok" : "error");
        fflush(stdout);
    }

    release_devices(&devices);
    ei_unref(ei);
    g_object_unref(connection);
    return EXIT_SUCCESS;
}
