<?php
/** A separate classic checkout: existing pages, vendor files and Store API stay intact. */
defined('ABSPATH') || exit;

final class Afrand_Faraz_Checkout_Compatibility {
    const OPTION = 'afrand_faraz_classic_checkout';

    static function page() {
        $settings = get_option(self::OPTION, array());
        if (empty($settings['enabled']) || empty($settings['page_id'])) return null;
        $page = get_post((int) $settings['page_id']);
        // Fail closed if an administrator replaces our classic form with blocks.
        return $page && $page->post_status === 'publish' && $page->post_type === 'page'
            && has_shortcode($page->post_content, 'woocommerce_checkout')
            && !has_block('woocommerce/checkout', $page) ? $page : null;
    }

    static function classic_request($page) {
        if ((defined('REST_REQUEST') && REST_REQUEST) || is_admin()) return false;
        // Classic AJAX is shared by all forms. Only recognize our same-origin form.
        $ajax = isset($_GET['wc-ajax']) && is_string($_GET['wc-ajax']) ? $_GET['wc-ajax'] : '';
        if (in_array($ajax, array('checkout', 'update_order_review'), true)) {
            // WooCommerce replaces the hidden _wp_http_referer during review.
            // At submission it points to wc-ajax, not the originating form.
            $referer = isset($_SERVER['HTTP_REFERER']) && is_string($_SERVER['HTTP_REFERER'])
                ? $_SERVER['HTTP_REFERER'] : wp_get_referer();
            $own_url = get_permalink($page->ID);
            if (!$referer || wp_parse_url($referer, PHP_URL_HOST) !== wp_parse_url($own_url, PHP_URL_HOST)) return false;
            return self::own_path($referer, $page);
        }
        if (isset($_GET['page_id']) && (int) $_GET['page_id'] === (int) $page->ID) return true;
        return self::own_path(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '', $page);
    }

    static function own_path($url, $page) {
        $path = wp_parse_url($url, PHP_URL_PATH);
        $own = wp_parse_url(get_permalink($page->ID), PHP_URL_PATH);
        if (!$path || !$own || $own === '/') return false;
        $path = trailingslashit(rawurldecode($path));
        $own = trailingslashit(rawurldecode($own));
        return strpos($path, $own) === 0;
    }

    static function page_id($original) {
        $page = self::page();
        return $page && self::classic_request($page) ? (int) $page->ID : $original;
    }

    static function url($original) {
        $page = self::page();
        $settings = get_option(self::OPTION, array());
        return $page && (!empty($settings['route_store']) || self::classic_request($page)) ? get_permalink($page->ID) : $original;
    }

    static function redirect_original() {
        $page = self::page();
        $settings = get_option(self::OPTION, array());
        if (empty($settings['route_store'])) return;
        $original = (int) get_option('woocommerce_checkout_page_id');
        if ($page && $original !== (int) $page->ID && is_page($original) && !is_wc_endpoint_url()) {
            wp_safe_redirect(get_permalink($page->ID), 302);
            exit;
        }
    }

    static function robots($robots) {
        $page = self::page();
        if ($page && is_page($page->ID)) $robots['noindex'] = true;
        return $robots;
    }

    static function menu() {
        add_submenu_page('woocommerce', 'تنظیم اتصال کیف پول فراز', 'اتصال کیف پول فراز', 'manage_options', 'afrand-faraz-setup', array(__CLASS__, 'render'));
    }

    static function render() {
        if (!current_user_can('manage_options')) wp_die('دسترسی مجاز نیست.', '', array('response'=>403));
        $message = '';
        if (isset($_POST['afrand_checkout_save'])) {
            check_admin_referer('afrand_checkout_setup');
            $settings = get_option(self::OPTION, array());
            $enabled = isset($_POST['enabled']);
            if ($enabled && (!function_exists('WC') || !function_exists('farazsms_wallet_balance'))) {
                $message = 'ابتدا ووکامرس و کیف پول فراز را فعال کنید.';
            } else {
                if ($enabled && empty($settings['page_id'])) {
                    $id = wp_insert_post(array('post_type'=>'page', 'post_status'=>'publish', 'post_title'=>'تسویه حساب کیف پول', 'post_name'=>'afrand-wallet-checkout', 'post_content'=>'[woocommerce_checkout]', 'comment_status'=>'closed', 'ping_status'=>'closed'), true);
                    if (is_wp_error($id)) $message = 'ساخت برگه انجام نشد؛ دوباره بررسی کنید.';
                    else $settings['page_id'] = (int) $id;
                }
                if (!$message) {
                    $settings['enabled'] = $enabled;
                    $settings['route_store'] = $enabled && isset($_POST['route_store']);
                    update_option(self::OPTION, $settings, false);
                    $message = $enabled && !self::page() ? 'برگهٔ واسط معتبر نیست؛ محتوای آن باید شورت‌کد تسویه‌حساب کلاسیک باشد.' : 'تنظیمات ذخیره شد.';
                }
            }
        }
        $settings = get_option(self::OPTION, array());
        echo '<div class="wrap"><h1>اتصال کیف پول فراز</h1>';
        if ($message) echo '<div class="notice notice-info"><p>'.esc_html($message).'</p></div>';
        echo '<p>این گزینه یک برگهٔ جدا با فرم کلاسیک خود ووکامرس می‌سازد. به‌صورت پیش‌فرض مسیر خرید عمومی سایت تغییر نمی‌کند و برگهٔ جدا از لینک زیر قابل استفاده است. پرداخت، کسر کیف پول و کش‌بک با خود فراز انجام می‌شود. هدایت خرید عمومی فقط با انتخاب جداگانهٔ گزینهٔ زیر فعال می‌شود.</p>';
        echo '<form method="post">';
        wp_nonce_field('afrand_checkout_setup');
        echo '<p><label><input type="checkbox" name="enabled" value="1" '.checked(!empty($settings['enabled']), true, false).'> استفاده از تسویه‌حساب کلاسیک جداگانه</label></p>';
        echo '<p><label><input type="checkbox" name="route_store" value="1" '.checked(!empty($settings['route_store']), true, false).'> هدایت پرداخت عمومی سایت به این برگه (اختیاری؛ مسیر خرید سایت را تغییر می‌دهد)</label></p><p><button class="button button-primary" name="afrand_checkout_save" value="1">ذخیره تنظیمات</button></p></form>';
        $page = self::page();
        if ($page) echo '<p><a href="'.esc_url(get_permalink($page->ID)).'" target="_blank" rel="noopener">مشاهدهٔ تسویه‌حساب</a></p>';
        echo '<p>پیامک واریزهای جدید وب‌اپ از الگوی «شارژ کیف پول» خود فراز استفاده می‌کند. وضعیت هر پیامک در سوابق کیف پول دیده می‌شود. برای پرداخت باقی‌ماندهٔ سفارش، سایت باید درگاه پرداخت معتبر داشته باشد.</p></div>';
    }
}

add_action('admin_menu', array('Afrand_Faraz_Checkout_Compatibility', 'menu'), 99);
add_filter('woocommerce_get_checkout_page_id', array('Afrand_Faraz_Checkout_Compatibility', 'page_id'));
add_filter('woocommerce_get_checkout_url', array('Afrand_Faraz_Checkout_Compatibility', 'url'));
add_action('template_redirect', array('Afrand_Faraz_Checkout_Compatibility', 'redirect_original'));
add_filter('wp_robots', array('Afrand_Faraz_Checkout_Compatibility', 'robots'));
