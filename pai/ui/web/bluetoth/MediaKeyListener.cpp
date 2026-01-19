#include <windows.h>
#include <iostream>

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (msg == WM_APPCOMMAND) {
        int cmd = GET_APPCOMMAND_LPARAM(lParam);

        switch (cmd) {
            case APPCOMMAND_MEDIA_PLAY_PAUSE:
                std::cout << "Play/Pause\n";
                break;
            case APPCOMMAND_MEDIA_NEXTTRACK:
                std::cout << "Next Track\n";
                break;
            case APPCOMMAND_MEDIA_PREVIOUSTRACK:
                std::cout << "Previous Track\n";
                break;
            case APPCOMMAND_VOLUME_UP:
                std::cout << "Volume Up\n";
                break;
            case APPCOMMAND_VOLUME_DOWN:
                std::cout << "Volume Down\n";
                break;
            case APPCOMMAND_MEDIA_STOP:
                std::cout << "Stop\n";
                break;
            default:
                std::cout << "Other media command: " << cmd << "\n";
        }
    }

    return DefWindowProc(hwnd, msg, wParam, lParam);
}

int main() {
    const char CLASS_NAME[] = "MediaListenerWindow";

    WNDCLASS wc = {};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = GetModuleHandle(NULL);
    wc.lpszClassName = CLASS_NAME;

    RegisterClass(&wc);

    HWND hwnd = CreateWindowEx(
        0,
        CLASS_NAME,
        "Hidden Media Listener",
        0, 0, 0, 0, 0,
        HWND_MESSAGE, NULL, GetModuleHandle(NULL), NULL
    );

    if (hwnd == NULL) {
        return 1;
    }

    std::cout << "Listening for AVRCP media commands... (Press Ctrl+C to exit)\n";

    MSG msg = {};
    while (GetMessage(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    return 0;
}
