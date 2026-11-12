//! A tiny crate for the mutation gate.

pub fn clamp(value: i32, low: i32, high: i32) -> i32 {
    if value < low {
        low
    } else if value > high {
        high
    } else {
        value
    }
}

pub fn is_even(value: i32) -> bool {
    value % 2 == 0
}

pub fn untested(value: i32) -> i32 {
    value + 1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clamps_below_and_above() {
        assert_eq!(clamp(-5, 0, 10), 0);
        assert_eq!(clamp(50, 0, 10), 10);
        assert_eq!(clamp(5, 0, 10), 5);
    }

    #[test]
    fn even_numbers() {
        assert!(is_even(4));
    }
}
