<?php
/** Read-only view of the native Faraz wallet, without copying or issuing credit. */
defined('ABSPATH') || exit;

final class Afrand_Faraz_Wallet_Admin {
    const SLUG = 'afrand-faraz-wallet-users';

    static function register() {
        add_submenu_page('farazsms', 'موجودی کیف پول کاربران', 'موجودی کیف پول کاربران', 'manage_woocommerce', self::SLUG, array(__CLASS__, 'render'));
        add_submenu_page('woocommerce', 'موجودی کیف پول فراز', 'موجودی کیف پول فراز', 'manage_woocommerce', self::SLUG, array(__CLASS__, 'render'));
    }

    static function url($args = array()) {
        return add_query_arg(array_merge(array('page' => self::SLUG), $args), admin_url('admin.php'));
    }

    static function money($value) {
        $currency = get_woocommerce_currency();
        $label = array('IRT' => 'تومان', 'IRR' => 'ریال');
        return number_format_i18n((float) $value, floor((float) $value) == (float) $value ? 0 : 2) . ' ' . (isset($label[$currency]) ? $label[$currency] : $currency);
    }

    static function phone($id) {
        foreach (array('afrand_profile_phone', 'billing_phone', 'phone', 'digits_phone_no') as $key) {
            $value = get_user_meta($id, $key, true);
            if ($value !== '') return $value;
        }
        $user = get_userdata($id);
        return $user && preg_match('/^\+?\d{10,13}$/', $user->user_login) ? $user->user_login : '—';
    }

    static function pagination($page, $total, $args) {
        $pages = (int) ceil($total / 20);
        if ($pages <= 1) return;
        echo '<p>' . esc_html(sprintf('صفحه %s از %s', $page, $pages)) . ' ';
        if ($page > 1) echo '<a class="button" href="' . esc_url(self::url(array_merge($args, array('paged' => $page - 1)))) . '">قبلی</a> ';
        if ($page < $pages) echo '<a class="button" href="' . esc_url(self::url(array_merge($args, array('paged' => $page + 1)))) . '">بعدی</a>';
        echo '</p>';
    }

    static function render() {
        if (!current_user_can('manage_woocommerce')) wp_die('دسترسی مجاز نیست.', '', array('response' => 403));
        echo '<div class="wrap"><h1>موجودی کیف پول کاربران فراز</h1>';
        if (!function_exists('farazsms_wallet_balance') || !function_exists('farazsms_wallet_table') || !function_exists('get_woocommerce_currency')) {
            echo '<div class="notice notice-error"><p>افزونه فراز و ووکامرس باید فعال باشند.</p></div></div>';
            return;
        }
        echo '<p>این صفحه موجودی واقعی کیف پول فراز را نشان می‌دهد. واریز مستقیم و مبالغ کش‌بک محاسبه‌شده در برنامه، همگی در همین کیف پول ثبت می‌شوند. بخش کش‌بک خود فراز، دفتر جداگانه‌ای دارد.</p>';
        $id = isset($_GET['user_id']) ? absint($_GET['user_id']) : 0;
        $page = isset($_GET['paged']) ? max(1, min(100000, absint($_GET['paged']))) : 1;
        if ($id) self::history($id, $page);
        else self::users($page);
        echo '</div>';
    }

    static function users($page) {
        global $wpdb;
        $search = isset($_GET['s']) && is_scalar($_GET['s']) ? sanitize_text_field(wp_unslash($_GET['s'])) : '';
        $search = function_exists('mb_substr') ? mb_substr($search, 0, 100) : substr($search, 0, 100);
        $search = strtr($search, array('۰'=>'0','۱'=>'1','۲'=>'2','۳'=>'3','۴'=>'4','۵'=>'5','۶'=>'6','۷'=>'7','۸'=>'8','۹'=>'9','٠'=>'0','١'=>'1','٢'=>'2','٣'=>'3','٤'=>'4','٥'=>'5','٦'=>'6','٧'=>'7','٨'=>'8','٩'=>'9'));
        echo '<form method="get"><input type="hidden" name="page" value="' . esc_attr(self::SLUG) . '"><label for="wallet-search">نام، موبایل یا شناسه کاربر: </label><input id="wallet-search" name="s" value="' . esc_attr($search) . '"> <button class="button">جستجو</button></form>';
        $where = '1=1';
        if ($search !== '') {
            $like = '%' . $wpdb->esc_like($search) . '%';
            $where = $wpdb->prepare("(u.display_name LIKE %s OR u.user_login LIKE %s OR CAST(u.ID AS CHAR) = %s OR EXISTS (SELECT 1 FROM {$wpdb->usermeta} m WHERE m.user_id=u.ID AND m.meta_key IN ('afrand_profile_phone','billing_phone','phone','digits_phone_no') AND m.meta_value LIKE %s))", $like, $like, $search, $like);
        }
        $total = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->users} u WHERE $where");
        if ($wpdb->last_error) { echo '<p>خواندن کاربران با خطا روبه‌رو شد.</p>'; return; }
        $page = min($page, max(1, (int) ceil($total / 20)));
        $users = $wpdb->get_results($wpdb->prepare("SELECT u.ID,u.display_name FROM {$wpdb->users} u WHERE $where ORDER BY u.ID DESC LIMIT %d OFFSET %d", 20, ($page - 1) * 20));
        if ($wpdb->last_error) { echo '<p>خواندن کاربران با خطا روبه‌رو شد؛ دوباره تلاش کنید.</p>'; return; }
        update_meta_cache('user', wp_list_pluck($users, 'ID'));
        echo '<p>همه کاربران، شامل کاربران با موجودی صفر — تعداد نتیجه: ' . esc_html($total) . '</p><div style="overflow:auto"><table class="widefat striped"><thead><tr><th>شناسه</th><th>نام</th><th>موبایل</th><th>موجودی قابل استفاده</th><th>جزئیات</th></tr></thead><tbody>';
        foreach ($users as $user) {
            $balance = farazsms_wallet_balance($user->ID);
            $balance_error = (bool) $wpdb->last_error;
            echo '<tr><td>' . esc_html($user->ID) . '</td><td>' . esc_html($user->display_name) . '</td><td dir="ltr">' . esc_html(self::phone($user->ID)) . '</td><td>' . esc_html($balance_error ? 'خطا در دریافت موجودی' : self::money($balance)) . '</td><td><a href="' . esc_url(self::url(array('user_id' => $user->ID))) . '">سوابق کیف پول</a></td></tr>';
        }
        if (!$users) echo '<tr><td colspan="5">کاربری پیدا نشد.</td></tr>';
        echo '</tbody></table></div>';
        self::pagination($page, $total, array('s' => $search));
    }

    static function history($id, $page) {
        global $wpdb;
        $user = get_userdata($id);
        echo '<p><a href="' . esc_url(self::url()) . '">بازگشت به فهرست کاربران</a></p>';
        if (!$user) { echo '<p>کاربر پیدا نشد.</p>'; return; }
        $balance = farazsms_wallet_balance($id);
        if ($wpdb->last_error) { echo '<p>دریافت موجودی با خطا روبه‌رو شد.</p>'; return; }
        echo '<h2>' . esc_html($user->display_name . ' — ' . self::phone($id)) . '</h2><p>موجودی قابل استفاده: <strong>' . esc_html(self::money($balance)) . '</strong></p>';
        $table = farazsms_wallet_table();
        $total = (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM $table WHERE user_id=%d", $id));
        if ($wpdb->last_error) { echo '<p>خواندن سوابق با خطا روبه‌رو شد.</p>'; return; }
        $page = min($page, max(1, (int) ceil($total / 20)));
        $rows = $wpdb->get_results($wpdb->prepare("SELECT id,amount,used_amount,status,description,source,created_at,expires_at FROM $table WHERE user_id=%d ORDER BY id DESC LIMIT %d OFFSET %d", $id, 20, ($page - 1) * 20));
        if ($wpdb->last_error) { echo '<p>خواندن سوابق با خطا روبه‌رو شد.</p>'; return; }
        $sms=array();
        if (class_exists('Afrand_Faraz_Credit_Notifications')) {
            $keys=array();
            foreach ($rows as $row) if (preg_match('/\[AFRAND:([a-f0-9]{64})\]/',(string)$row->description,$match)) $keys[]=$match[1];
            if ($keys) {
                $placeholders=implode(',',array_fill(0,count($keys),'%s'));
                $notifications=$wpdb->get_results($wpdb->prepare('SELECT operation_key,sms_state FROM '.Afrand_Faraz_Wallet_Bridge::table()." WHERE operation_key IN ($placeholders)",$keys));
                foreach ((array)$notifications as $notification) $sms[$notification->operation_key]=$notification->sms_state;
            }
        }
        echo '<p>مبلغ مصرف‌شده، مصرف از هر اعتبار را نشان می‌دهد. اعتبار منقضی یا غیرفعال در موجودی قابل استفاده محاسبه نمی‌شود. وضعیت پیامک فقط برای شارژهای جدید برنامه است؛ پذیرش سامانه به معنی تأیید تحویل به گوشی نیست.</p><div style="overflow:auto"><table class="widefat striped"><thead><tr><th>شناسه</th><th>تاریخ واریز</th><th>مبلغ واریز</th><th>مصرف‌شده</th><th>وضعیت</th><th>انقضا</th><th>توضیحات</th><th>پیامک شارژ</th></tr></thead><tbody>';
        foreach ($rows as $row) {
            $expired = $row->expires_at && $row->expires_at <= current_time('mysql');
            $status = $row->status === 'active' ? ($expired ? 'منقضی' : 'فعال') : $row->status;
            $description = preg_replace('/\s*\[AFRAND:[a-f0-9]{64}\]/i', '', (string) $row->description);
            $sms_label='—';
            if (preg_match('/\[AFRAND:([a-f0-9]{64})\]/',(string)$row->description,$match) && isset($sms[$match[1]])) {
                $labels=Afrand_Faraz_Credit_Notifications::labels();
                $sms_label=isset($labels[$sms[$match[1]]]) ? $labels[$sms[$match[1]]] : 'نیازمند بررسی';
            }
            echo '<tr>';
            foreach (array($row->id, $row->created_at, self::money($row->amount), self::money($row->used_amount), $status, $row->expires_at ?: 'بدون انقضا', $description, $sms_label) as $value) echo '<td>' . esc_html($value) . '</td>';
            echo '</tr>';
        }
        if (!$rows) echo '<tr><td colspan="8">واریزی ثبت نشده است.</td></tr>';
        echo '</tbody></table></div>';
        self::pagination($page, $total, array('user_id' => $id));
    }
}
